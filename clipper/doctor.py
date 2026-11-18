"""Health checks shown on the dashboard's Setup page and by `python -m clipper doctor`."""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import Config

log = logging.getLogger(__name__)

OK, WARN, FAIL, OFF = "ok", "warn", "fail", "off"


@dataclass
class Check:
    name: str
    status: str   # ok | warn | fail | off (not configured, optional)
    detail: str
    fix: str = ""


def _tools() -> Check:
    missing = [t for t in ("ffmpeg", "ffprobe", "streamlink") if not shutil.which(t)]
    if missing:
        return Check("Video tools", FAIL, f"missing: {', '.join(missing)}", "Rebuild the Docker image.")
    return Check("Video tools", OK, "ffmpeg and streamlink installed")


def _disk(cfg: Config) -> Check:
    free = shutil.disk_usage(cfg.data_dir).free / 1e9
    if free < 5:
        return Check("Disk space", FAIL, f"{free:.1f} GB free", "Delete old clips in data/clips or grow the disk.")
    if free < 15:
        return Check("Disk space", WARN, f"{free:.1f} GB free", "Old clips in data/clips can be deleted.")
    return Check("Disk space", OK, f"{free:.0f} GB free")


def _twitch() -> Check:
    cid, secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    if not (cid and secret):
        return Check("Twitch", FAIL, "not set up: Twitch streamers can't be watched, no suggestions",
                     "Create a Twitch app and paste its Client ID and Secret below.")
    try:
        r = httpx.post("https://id.twitch.tv/oauth2/token", params={
            "client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}, timeout=15)
    except httpx.HTTPError as e:
        return Check("Twitch", WARN, f"couldn't reach Twitch: {e}")
    if r.status_code == 200:
        return Check("Twitch", OK, "keys work")
    return Check("Twitch", FAIL, f"Twitch rejected the keys ({r.status_code})", "Re-copy the Client ID and make a New Secret.")


def _anthropic(cfg: Config) -> Check:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return Check("Claude (AI titles)", OFF, "not set up: clips get simple template titles and no AI score",
                     "Optional: paste a Claude API key below.")
    import anthropic
    try:
        anthropic.Anthropic(max_retries=1, timeout=20).models.retrieve(cfg.llm.model)
        return Check("Claude (AI titles)", OK, f"key works, model {cfg.llm.model}")
    except anthropic.AuthenticationError:
        return Check("Claude (AI titles)", FAIL, "the API key was rejected", "Create a new key at console.anthropic.com.")
    except anthropic.NotFoundError:
        return Check("Claude (AI titles)", FAIL, f"model {cfg.llm.model} not found", "Change llm.model in the settings.")
    except anthropic.APIError as e:
        return Check("Claude (AI titles)", WARN, f"couldn't verify: {e}")


def _youtube(cfg: Config) -> Check:
    enabled = (cfg.uploads.targets.get("youtube") or {}).get("enabled")
    if not enabled:
        return Check("YouTube Shorts", OFF, "turned off in settings")
    need = [k for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET") if not os.environ.get(k)]
    if need:
        return Check("YouTube Shorts", FAIL, "not set up", "Create a Google OAuth client and paste its ID and secret below.")
    if not os.environ.get("YOUTUBE_REFRESH_TOKEN"):
        return Check("YouTube Shorts", FAIL, "not connected yet", "Press Connect YouTube below.")
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YOUTUBE_CLIENT_ID"], "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=20)
    j = r.json()
    if "access_token" not in j:
        return Check("YouTube Shorts", FAIL, f"Google rejected the login ({j.get('error')})",
                     "Press Connect YouTube again. If it keeps expiring after 7 days, publish the OAuth consent screen.")
    ch = httpx.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                   headers={"Authorization": f"Bearer {j['access_token']}"}, timeout=20)
    items = ch.json().get("items") if ch.status_code == 200 else None
    if items:
        return Check("YouTube Shorts", OK, f"connected to channel \"{items[0]['snippet']['title']}\"")
    if ch.status_code == 200:
        return Check("YouTube Shorts", FAIL, "this Google account has no YouTube channel",
                     "Create a channel at youtube.com, then press Connect YouTube again.")
    return Check("YouTube Shorts", OK, "connected")


def _tiktok(cfg: Config, db) -> Check:
    enabled = (cfg.uploads.targets.get("tiktok") or {}).get("enabled")
    if not enabled:
        return Check("TikTok", OFF, "turned off in settings")
    from .uploaders.tiktok import TikTokUploader
    up = TikTokUploader(cfg.uploads.targets.get("tiktok") or {}, db)
    ok, why = up.configured()
    if not ok:
        if "CLIENT" in why:
            return Check("TikTok", FAIL, "not set up", "Create a TikTok developer app and paste its key and secret below.")
        return Check("TikTok", FAIL, "not connected yet", "Press Connect TikTok below.")
    try:
        token = up._access_token()
        r = httpx.get("https://open.tiktokapis.com/v2/user/info/", params={"fields": "display_name"},
                      headers={"Authorization": f"Bearer {token}"}, timeout=20)
        name = ((r.json().get("data") or {}).get("user") or {}).get("display_name")
    except Exception as e:  # noqa: BLE001
        return Check("TikTok", FAIL, f"login failed: {e}", "Press Connect TikTok again.")
    mode = (cfg.uploads.targets.get("tiktok") or {}).get("mode", "inbox")
    where = "drafts inbox" if mode == "inbox" else "your profile"
    return Check("TikTok", OK, f"connected{f' as {name}' if name else ''}; clips go to your {where}")


def _metricool(cfg: Config, public_url: str | None) -> Check:
    enabled = (cfg.uploads.targets.get("metricool") or {}).get("enabled")
    if not enabled:
        return Check("Metricool", OFF, "turned off in settings")
    need = [k for k in ("METRICOOL_USER_TOKEN", "METRICOOL_USER_ID", "METRICOOL_BLOG_ID") if not os.environ.get(k)]
    if need:
        return Check("Metricool", FAIL, f"missing {', '.join(need)}", "Paste them below (Metricool Advanced plan).")
    if not public_url:
        return Check("Metricool", FAIL, "PUBLIC_BASE_URL is not set", "Re-run the installer to set up HTTPS.")
    try:
        r = httpx.get("https://app.metricool.com/api/admin/simpleProfiles",
                      params={"userId": os.environ["METRICOOL_USER_ID"]},
                      headers={"X-Mc-Auth": os.environ["METRICOOL_USER_TOKEN"]}, timeout=20)
    except httpx.HTTPError as e:
        return Check("Metricool", WARN, f"couldn't reach Metricool: {e}")
    if r.status_code in (401, 403):
        return Check("Metricool", FAIL, f"Metricool refused the token ({r.status_code})",
                     "Re-copy the API token and user ID (Account settings > API). The API needs the Advanced plan.")
    if r.status_code != 200:
        return Check("Metricool", WARN, f"Metricool answered {r.status_code}")
    try:
        brands = r.json()
    except ValueError:
        brands = None
    if isinstance(brands, dict):
        brands = brands.get("data")
    ids = [str(b.get("id", b.get("blogId"))) for b in brands or [] if isinstance(b, dict) and ("id" in b or "blogId" in b)] \
        if isinstance(brands, list) else []
    blog = os.environ["METRICOOL_BLOG_ID"].strip()
    if ids and blog not in ids:
        return Check("Metricool", FAIL, f"brand (blogId) {blog} isn't one of your Metricool brands",
                     "Copy blogId from the address bar while that brand is open in Metricool.")
    return Check("Metricool", OK, "token works")


def _public_url(public_url: str | None) -> Check:
    if not public_url:
        return Check("Public HTTPS address", WARN, "not set", "Re-run the installer, or set PUBLIC_BASE_URL in .env.")
    try:
        r = httpx.get(public_url.rstrip("/") + "/healthz", timeout=15)
        if r.status_code == 200:
            return Check("Public HTTPS address", OK, public_url)
        return Check("Public HTTPS address", WARN, f"{public_url} answered {r.status_code}")
    except httpx.HTTPError as e:
        why = type(e).__name__
        fix = ("If the dashboard opens fine in your browser, ignore this (some servers can't reach their own "
               "public address). Otherwise ports 80 and 443 need to be open in the Oracle security list; "
               "the Cloud Shell script does that.")
        if "certificate" in str(e).lower() or "ssl" in str(e).lower():
            fix = ("No certificate yet. It can take a few minutes; if it lasts hours, the free certificate "
                   "quota for this address is used up: open the backup address (clipper status shows it) and "
                   "press 'Use ... as the main address' on this page.")
        return Check("Public HTTPS address", FAIL, f"{public_url} not reachable ({why})", fix)


def _whisper(cfg: Config) -> Check:
    hf = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    if any(hf.glob(f"models--*faster-whisper-{cfg.whisper.model}*")):
        return Check("Captions model", OK, f"whisper '{cfg.whisper.model}' downloaded")
    return Check("Captions model", WARN, f"whisper '{cfg.whisper.model}' not downloaded yet",
                 "It downloads automatically with the first clip.")


def _streamers(cfg: Config) -> Check:
    on = [s for s in cfg.streamers if s.enabled]
    granted = [s.name for s in on if s.permission == "granted"]
    if not on:
        return Check("Streamers", FAIL, "no streamers enabled", "Add streamers in Settings.")
    if cfg.require_permission_to_upload and not granted:
        return Check("Streamers", WARN, f"watching {len(on)}; none has permission: granted, so nothing will upload",
                     "Mark streamers who allowed clipping as permission: granted in Settings.")
    return Check("Streamers", OK, f"watching {len(on)}; uploads allowed for {', '.join(granted) or 'everyone'}")


def run_checks(cfg: Config, db, public_url: str | None = None) -> list[Check]:
    public_url = public_url or os.environ.get("PUBLIC_BASE_URL")
    checks = []
    for fn in (_tools, lambda: _disk(cfg), _twitch, lambda: _anthropic(cfg), lambda: _youtube(cfg),
               lambda: _tiktok(cfg, db), lambda: _metricool(cfg, public_url), lambda: _public_url(public_url),
               lambda: _whisper(cfg), lambda: _streamers(cfg)):
        t = time.time()
        try:
            checks.append(fn())
        except Exception as e:  # noqa: BLE001 - one broken check must not hide the others
            log.exception("health check failed")
            checks.append(Check(getattr(fn, "__name__", "check"), WARN, f"check crashed: {e}"))
        log.debug("check took %.1fs", time.time() - t)
    return checks
