"""Command line entry point.

  python -m clipper run                      # the 24/7 bot + review dashboard
  python -m clipper check                    # who on your list is live on GTA 6 right now
  python -m clipper discover                 # biggest GTA 6 streams on Twitch right now
  python -m clipper clip-file VIDEO [--at S] # test detection/editing on a downloaded VOD
  python -m clipper doctor                   # what's connected, what's missing
  python -m clipper warmup                   # download the captions model ahead of the first clip
  python -m clipper auth-youtube             # terminal version of the dashboard's Connect YouTube
  python -m clipper auth-tiktok
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv


def _load_saved_keys() -> None:
    """Keys pasted on the dashboard's Setup page live in <data dir>/secrets.json and win over .env."""
    from .settings import Settings

    try:
        Settings(Path(os.environ.get("CLIPPER_DATA_DIR", "./data")))
    except OSError:
        pass


def _setup_logging() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    for noisy in ("httpx", "httpcore", "googleapiclient", "faster_whisper", "chat_downloader"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_run(args) -> None:
    import uvicorn

    from .dashboard import create_app
    from .orchestrator import Orchestrator

    orch = Orchestrator(args.config)
    app = create_app(orch)
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    threading.Thread(target=lambda: uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning"),
                     daemon=True, name="dashboard").start()
    logging.getLogger("clipper").info("dashboard on http://0.0.0.0:%d", port)
    orch.run_forever()


def cmd_check(args) -> None:
    from .orchestrator import Orchestrator

    orch = Orchestrator(args.config)
    live = orch.check_live()
    for s in orch.cfg.streamers:
        info = orch.last_check.get(s.name, {}).get("platforms", {})
        flag = "LIVE on GTA 6" if s.name in live else "not live on GTA 6"
        print(f"{s.name:20s} {flag:20s} permission={s.permission:8s} {info}")


def cmd_doctor(args) -> None:
    from . import db as dbm
    from .config import load_config
    from .doctor import run_checks
    from .settings import Settings

    cfg = load_config(args.config)
    Settings(cfg.data_dir)  # apply keys saved from the dashboard
    icon = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "off": "--  "}
    worst = 0
    for c in run_checks(cfg, dbm.DB(cfg.data_dir / "clipper.db")):
        print(f"[{icon[c.status]}] {c.name:22s} {c.detail}")
        if c.fix and c.status != "ok":
            print(f"       -> {c.fix}")
        worst = max(worst, {"fail": 2, "warn": 1}.get(c.status, 0))
    sys.exit(1 if worst == 2 else 0)


def cmd_warmup(args) -> None:
    from .config import load_config
    from .transcribe import _get_model

    cfg = load_config(args.config)
    _get_model(cfg.whisper)
    print(f"whisper model '{cfg.whisper.model}' is ready")


def cmd_discover(args) -> None:
    from .platforms.twitch import TwitchAPI

    tw = TwitchAPI(os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET"))
    if not tw.configured:
        sys.exit("Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET first (see SETUP.md).")
    for info in tw.top_gta6_streams(args.lang or None, args.top):
        print(f"{info.viewers:>8,}  {info.extra.get('language', ''):3s} twitch.tv/{info.channel:25s} {info.title[:70]}")


def cmd_clip_file(args) -> None:
    """Offline test: run audio-based detection (or a fixed timestamp) over a local video and render clips."""
    from . import db as dbm
    from .audio import loudness_series
    from .config import load_config
    from .detector import Moment, MomentDetector
    from .pipeline import ClipProcessor, Job
    from .platforms.base import LiveInfo

    cfg = load_config(args.config)
    db = dbm.DB(cfg.data_dir / "clipper.db")
    streamer = next((s for s in cfg.streamers if s.name.lower() == (args.streamer or "").lower()), None) \
        or (cfg.streamers[0] if cfg.streamers else None)
    if streamer is None:
        sys.exit("No streamers in config.yaml")
    platform, channel = next(iter(streamer.platforms.items()), ("twitch", streamer.name))
    info = LiveInfo(platform, channel, True, args.video, title=args.title or "GTA 6")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                args.video], capture_output=True, text=True, check=True).stdout.strip())
    if args.at is not None:
        d = cfg.detection
        moments = [Moment(start=max(0, args.at - d.clip_before_seconds), end=min(dur, args.at + d.clip_after_seconds),
                          event_ts=args.at, score=0, signals={"manual": True})]
    else:
        if args.warmup is not None:
            cfg.detection.warmup_seconds = args.warmup
        det = MomentDetector(cfg.detection, started_at=0.0)
        for ts, db_ in loudness_series(args.video, 0.0):
            det.add_audio(ts, db_)
        moments = []
        t = 0.0
        while t <= dur + det.eval_lag + 20:
            m = det.tick(t)
            if m:
                m.start, m.end = max(0.0, m.start), min(dur, m.end)
                moments.append(m)
            t += 1.0
        print(f"found {len(moments)} moment(s) using audio only (chat isn't available offline)")
    proc = ClipProcessor(cfg, db)
    for m in moments[: args.max]:
        cid = db.add_clip(streamer=streamer.name, platform=platform, channel=channel, stream_url=args.video,
                          stream_title=info.title, moment_at=m.event_ts, score=m.score, signals=m.signals)
        print(f"clip {cid}: {m.start:.1f}s -> {m.end:.1f}s (score {m.score})")
        proc.process(Job(cid, streamer, info, m, Path(args.video), m.start, m.end - m.start,
                         keep_raw=True))
        c = db.get_clip(cid)
        print(f"   -> {c['final_path']}\n      title: {c['title']}\n      status: {c['status']}")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    _load_saved_keys()
    _setup_logging()
    p = argparse.ArgumentParser(prog="clipper", description="GTA 6 stream clipping bot")
    p.add_argument("--config", default=os.environ.get("CLIPPER_CONFIG", "config.yaml"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the bot and dashboard").set_defaults(fn=cmd_run)
    sub.add_parser("check", help="check who is live on GTA 6").set_defaults(fn=cmd_check)
    d = sub.add_parser("discover", help="list the biggest GTA 6 streams on Twitch")
    d.add_argument("--lang", action="append", help="language code, e.g. en (repeatable)")
    d.add_argument("--top", type=int, default=25)
    d.set_defaults(fn=cmd_discover)
    c = sub.add_parser("clip-file", help="test clipping/editing on a local video file")
    c.add_argument("video")
    c.add_argument("--streamer", help="streamer name from config (for facecam/credit)")
    c.add_argument("--at", type=float, help="make one clip around this second instead of auto-detecting")
    c.add_argument("--title", help="stream title to give the AI")
    c.add_argument("--warmup", type=int, help="override detection warmup seconds")
    c.add_argument("--max", type=int, default=5, help="max clips to render")
    c.set_defaults(fn=cmd_clip_file)
    sub.add_parser("doctor", help="check keys, connections and disk").set_defaults(fn=cmd_doctor)
    sub.add_parser("warmup", help="download the captions model").set_defaults(fn=cmd_warmup)
    y = sub.add_parser("auth-youtube", help="connect YouTube with a code (Google device flow)")
    y.set_defaults(fn=lambda a: __import__("clipper.uploaders.youtube", fromlist=["x"]).run_auth_flow())
    t = sub.add_parser("auth-tiktok", help="get a TikTok refresh token")
    t.set_defaults(fn=lambda a: __import__("clipper.uploaders.tiktok", fromlist=["x"]).run_auth_flow())
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
