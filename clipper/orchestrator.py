"""Main loop: finds which streamers are live on GTA 6, runs workers, uploads approved clips."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from . import db as dbm
from . import notify
from .config import Config, Streamer, load_config
from .pipeline import ClipProcessor, StreamWorker
from .platforms.base import LiveInfo
from .platforms.kick import KickLive
from .platforms.twitch import TwitchAPI
from .platforms.youtube import YouTubeLive
from .settings import Settings
from .uploaders import build_uploaders

log = logging.getLogger(__name__)


class UploadManager(threading.Thread):
    """Uploads APPROVED clips to every enabled target, respecting daily caps and spacing."""

    def __init__(self, orch: "Orchestrator"):
        super().__init__(daemon=True, name="uploader")
        self.orch = orch

    def run(self) -> None:
        while True:
            try:
                self.step()
            except Exception:  # noqa: BLE001
                log.exception("upload step failed")
            time.sleep(30)

    def step(self) -> None:
        cfg, db = self.orch.cfg, self.orch.db
        uploaders = self.orch.uploaders
        if not uploaders:
            return
        now = time.time()
        if db.uploads_since(now - 86400) >= cfg.uploads.max_per_day:
            return
        if now - db.last_upload_at() < cfg.uploads.min_minutes_between * 60:
            return
        clip = db.next_approved()
        if not clip:
            return
        streamer = self.orch.streamer_by_name(clip["streamer"])
        if cfg.require_permission_to_upload and not (streamer and streamer.upload_allowed):
            db.update_clip(clip["id"], status=dbm.NEEDS_PERMISSION)
            return
        db.update_clip(clip["id"], status=dbm.UPLOADING)
        results = dict(clip.get("uploads") or {})
        errors = []
        for up in uploaders:
            if results.get(up.name, {}).get("ok"):
                continue
            try:
                res = up.upload(clip, Path(clip["final_path"]))
                results[up.name] = {"ok": True, "at": time.time(), **res}
                db.update_clip(clip["id"], uploads=results)  # saved now, so a restart can't post it twice
                log.info("clip %s uploaded to %s: %s", clip["id"], up.name, res)
            except Exception as e:  # noqa: BLE001
                log.exception("upload of clip %s to %s failed", clip["id"], up.name)
                results[up.name] = {"ok": False, "error": str(e)[:500]}
                errors.append(f"{up.name}: {e}")
        status = dbm.UPLOADED if not errors else dbm.FAILED
        db.update_clip(clip["id"], status=status, uploads=results, error="; ".join(errors) or None)
        if any(r.get("ok") and name != "local" for name, r in results.items()):
            notify.clip_posted(db, db.get_clip(clip["id"]), streamer.campaign_url if streamer else None)


class TunnelWatcher(threading.Thread):
    """On a home computer the public https address comes from a Cloudflare quick tunnel, which gets
    a new random *.trycloudflare.com name each time it starts. This asks the tunnel container for
    its current name and makes it the bot's public address (for Connect TikTok and Metricool)."""

    REPOST = 3 * 3600   # the address board keeps messages ~12 hours; repost well before that

    def __init__(self, orch: "Orchestrator", url: str):
        super().__init__(daemon=True, name="tunnel")
        self.orch, self.url = orch, url
        self.posted_at = 0.0

    def check(self) -> None:
        import httpx

        try:
            host = (httpx.get(self.url, timeout=5).json() or {}).get("hostname") or ""
        except (httpx.HTTPError, ValueError):
            return
        if not host:
            return
        address = f"https://{host}"
        changed = address != os.environ.get("PUBLIC_BASE_URL")
        if changed:
            os.environ["PUBLIC_BASE_URL"] = address
            self.orch.reload_integrations()
            log.info("public address is now %s", address)
        if changed or time.time() - self.posted_at > self.REPOST:
            if notify.publish_address(self.orch.db, address):
                self.posted_at = time.time()
            else:
                self.posted_at = time.time() - self.REPOST + 300   # try again in 5 minutes

    def run(self) -> None:
        while True:
            self.check()
            time.sleep(20)


class Orchestrator:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.cfg: Config = load_config(self.config_path)
        self._cfg_mtime = self.config_path.stat().st_mtime
        self.db = dbm.DB(self.cfg.data_dir / "clipper.db")
        self.settings = Settings(self.cfg.data_dir)  # keys pasted in the dashboard override .env
        self.youtube = YouTubeLive()
        self.kick = KickLive()
        self.processor = ClipProcessor(self.cfg, self.db)
        self.workers: dict[str, StreamWorker] = {}
        self.last_check: dict[str, dict] = {}
        self._last_discovery = 0.0
        self.reload_integrations()

    def reload_integrations(self) -> None:
        """(Re)builds API clients and uploaders from the current keys. Called after Setup changes."""
        self.public_base_url = os.environ.get("PUBLIC_BASE_URL")
        self.twitch = TwitchAPI(os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET"))
        self.uploaders = build_uploaders(self.cfg.uploads.targets, self.db, self.public_base_url)
        self._last_discovery = 0.0
        if not self.twitch.configured:
            log.warning("Twitch keys not set: Twitch live checks, viewer clips and discovery are off")

    def streamer_by_name(self, name: str) -> Streamer | None:
        return next((s for s in self.cfg.streamers if s.name == name), None)

    # --- config hot reload --------------------------------------------------------
    def _maybe_reload(self) -> None:
        try:
            mtime = self.config_path.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime == self._cfg_mtime:
            return
        try:
            new = load_config(self.config_path)
        except Exception as e:  # noqa: BLE001
            log.error("config.yaml has an error, keeping the old config: %s", e)
            return
        self._cfg_mtime = mtime
        self.cfg = new
        self.processor.cfg = new
        self.uploaders = build_uploaders(new.uploads.targets, self.db, self.public_base_url)
        for w in self.workers.values():  # permission/facecam changes apply to running streams too
            fresh = self.streamer_by_name(w.streamer.name)
            if fresh:
                w.streamer = fresh
        log.info("config reloaded (%d streamers)", len(new.streamers))

    # --- live checks --------------------------------------------------------------
    def check_live(self) -> dict[str, LiveInfo]:
        """Returns {streamer name: LiveInfo} for streamers live on GTA 6 (first matching platform)."""
        enabled = [s for s in self.cfg.streamers if s.enabled]
        twitch_logins = [s.platforms["twitch"] for s in enabled if "twitch" in s.platforms]
        tw = {}
        if twitch_logins and self.twitch.configured:
            try:
                tw = self.twitch.live_status(twitch_logins)
            except Exception as e:  # noqa: BLE001
                log.warning("twitch live check failed: %s", e)
        live: dict[str, LiveInfo] = {}
        for s in enabled:
            found = None
            checked = {}
            for platform, handle in s.platforms.items():
                if platform == "twitch":
                    info = tw.get(handle.lower())
                elif platform == "youtube":
                    info = self.youtube.live_status(handle)
                elif platform == "kick":
                    info = self.kick.live_status(handle)
                else:
                    continue
                if not info:
                    continue
                is_game = self.cfg.is_gta6(info.text_for_game_match) or not s.game_check
                checked[platform] = {"live": info.is_live, "gta6": is_game, "title": info.title,
                                     "game": info.game, "viewers": info.viewers}
                if info.is_live and is_game and not found:
                    found = info
            self.last_check[s.name] = {"at": time.time(), "platforms": checked}
            if found:
                live[s.name] = found
        return live

    def discover(self) -> None:
        d = self.cfg.discovery
        if not d.get("enabled") or not self.twitch.configured:
            return
        if time.time() - self._last_discovery < int(d.get("interval_minutes", 15)) * 60:
            return
        self._last_discovery = time.time()
        try:
            for info in self.twitch.top_gta6_streams(d.get("languages"), int(d.get("top", 25))):
                self.db.upsert_discovered("twitch", info.channel, info.extra.get("display_name") or info.channel,
                                          info.viewers, info.title, info.extra.get("language", ""))
        except Exception as e:  # noqa: BLE001
            log.warning("discovery failed: %s", e)

    # --- main loop ----------------------------------------------------------------
    def step(self) -> None:
        self._maybe_reload()
        for name, w in list(self.workers.items()):
            if w.done:
                del self.workers[name]
        live = self.check_live()
        if self.cfg.require_permission_to_upload and not self.cfg.clip_without_permission:
            live = {n: i for n, i in live.items() if (s := self.streamer_by_name(n)) and s.upload_allowed}
        # stop workers whose streamer is no longer live on GTA 6 or was disabled
        for name, w in list(self.workers.items()):
            if name not in live:
                log.info("%s is no longer live on GTA 6 (or not allowed to clip)", name)
                w.stop()
        # start new workers, biggest streams first, up to the concurrency limit
        for name, info in sorted(live.items(), key=lambda kv: -kv[1].viewers):
            if name in self.workers:
                self.workers[name].info.viewers = info.viewers
                continue
            if len(self.workers) >= self.cfg.max_concurrent_streams:
                log.info("at max_concurrent_streams; not starting %s", name)
                break
            s = self.streamer_by_name(name)
            w = StreamWorker(self.cfg, self.db, s, info, self.processor,
                             self.twitch if info.platform == "twitch" else None)
            self.workers[name] = w
            w.start()
        self.discover()

    def run_forever(self) -> None:
        if n := self.db.reset_interrupted_uploads():
            log.warning("%d upload(s) were interrupted by a restart; they're under Failed with a Retry button", n)
        self.processor.start()
        UploadManager(self).start()
        if url := os.environ.get("TUNNEL_METRICS_URL"):
            TunnelWatcher(self, url).start()
        names = ", ".join(s.name for s in self.cfg.streamers if s.enabled)
        log.info("clipper started; watching for: %s", names)
        log.info("review_mode=%s, upload targets: %s", self.cfg.review_mode,
                 ", ".join(u.name for u in self.uploaders) or "none")
        while True:
            try:
                self.step()
            except Exception:  # noqa: BLE001
                log.exception("main loop step failed")
            time.sleep(self.cfg.poll_interval_seconds)

    def status(self) -> dict:
        return {"workers": [w.status() for w in self.workers.values() if not w.done],
                "checks": self.last_check, "review_mode": self.cfg.review_mode,
                "uploaders": [u.name for u in self.uploaders]}
