"""Per-stream workers and the clip processing queue."""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import db as dbm
from . import notify
from .audio import loudness_series
from .chat import make_chat_source
from .config import Config, Facecam, Streamer
from .cutter import cut_from_segments
from .detector import Moment, MomentDetector
from .editor import render_vertical
from .facecam import detect_facecam, detect_from_segments
from .metadata import credit_line, judge_clip
from .platforms.base import LiveInfo
from .recorder import Recorder, Segment
from .transcribe import extract_wav, transcribe

log = logging.getLogger(__name__)


def slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", text).strip("_").lower() or "x"


@dataclass
class Job:
    clip_id: int
    streamer: Streamer
    info: LiveInfo
    moment: Moment
    raw: Path
    offset: float
    duration: float
    keep_raw: bool = False  # True when raw is the user's own file (clip-file command)
    facecam: Facecam | None = None  # webcam box found on the live stream


class ClipProcessor(threading.Thread):
    """Transcribe -> judge -> render, one clip at a time (CPU heavy)."""

    def __init__(self, cfg: Config, db: dbm.DB):
        super().__init__(daemon=True, name="processor")
        self.cfg = cfg
        self.db = db
        self.q: queue.Queue[Job] = queue.Queue()

    def submit(self, job: Job) -> None:
        self.q.put(job)

    def run(self) -> None:
        while True:
            job = self.q.get()
            try:
                self.process(job)
            except Exception as e:  # noqa: BLE001
                log.exception("processing clip %s failed", job.clip_id)
                self.db.update_clip(job.clip_id, status=dbm.FAILED, error=f"processing: {e}")

    def work_dir(self, job: Job) -> Path:
        if not job.keep_raw:
            return job.raw.parent
        d = self.cfg.data_dir / "clips" / f"{job.clip_id:05d}_{slug(job.streamer.name)}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def process(self, job: Job) -> None:
        cfg, s, info = self.cfg, job.streamer, job.info
        work = self.work_dir(job)
        wav = extract_wav(job.raw, job.offset, job.duration, work / "audio.wav")
        text, words = transcribe(wav, cfg.whisper, s.language)
        wav.unlink(missing_ok=True)

        meta = judge_clip(cfg.llm, s, info.platform, info.channel, info.title, text,
                          job.moment.chat_sample, job.moment.signals)
        final = work / "final.mp4"
        cam = s.facecam or job.facecam
        if not cam and cfg.edit.layout in ("auto", "split"):
            cam = detect_facecam(job.raw, job.offset, job.duration)
        render_vertical(job.raw, job.offset, job.duration, final, cfg.edit, cam, words,
                        credit_line(s, info.platform, info.channel))
        if not job.keep_raw:
            job.raw.unlink(missing_ok=True)

        if meta.llm_score is not None and meta.llm_score < cfg.llm.min_score:
            status = dbm.LOW_SCORE
        elif cfg.require_permission_to_upload and not s.upload_allowed:
            status = dbm.NEEDS_PERMISSION
        elif cfg.review_mode or meta.llm_score is None:
            # Without an AI score (no Claude key, or Claude failed) nothing has filtered the clip,
            # so it waits for a human even when review mode is off.
            status = dbm.READY
        else:
            status = dbm.APPROVED
        self.db.update_clip(job.clip_id, status=status, final_path=str(final), transcript=text,
                            title=meta.title, description=meta.description, hashtags=meta.hashtags,
                            llm_score=meta.llm_score, llm_reason=meta.llm_reason,
                            raw_path=str(job.raw) if job.keep_raw else None)
        log.info("clip %s ready [%s] score=%s: %s", job.clip_id, status, meta.llm_score, meta.title)
        if status == dbm.READY and not job.keep_raw:
            notify.clip_ready(self.db, self.db.get_clip(job.clip_id))


class StreamWorker(threading.Thread):
    """Records one live stream, listens to its chat, and cuts clips when moments are detected."""

    def __init__(self, cfg: Config, db: dbm.DB, streamer: Streamer, info: LiveInfo,
                 processor: ClipProcessor, twitch_api=None):
        super().__init__(daemon=True, name=f"worker-{slug(streamer.name)}")
        self.cfg, self.db, self.streamer, self.info = cfg, db, streamer, info
        self.processor = processor
        self.twitch_api = twitch_api
        self.started_at = time.time()
        self.detector = MomentDetector(cfg.detection, started_at=self.started_at)
        self.buffer_dir = cfg.data_dir / "buffer" / slug(streamer.name)
        for old in self.buffer_dir.glob("*.ts"):
            old.unlink(missing_ok=True)
        self.recorder = Recorder(info.url, self.buffer_dir, cfg.stream_quality,
                                 cfg.detection.segment_seconds, cfg.detection.buffer_seconds,
                                 on_segment=self._on_segment)
        self.chat = make_chat_source(info.platform, info.channel, {**info.extra, "url": info.url},
                                     self.detector.add_chat)
        self.pending: list[Moment] = []
        self.clips_made = 0
        self.facecam: Facecam | None = None    # webcam box found on this stream
        self._facecam_misses = 0
        self._facecam_thread: threading.Thread | None = None
        self._halt = threading.Event()

    # --- lifecycle ----------------------------------------------------------------
    def stop(self) -> None:
        self._halt.set()

    @property
    def done(self) -> bool:
        return not self.is_alive()

    def status(self) -> dict:
        return {"streamer": self.streamer.name, "platform": self.info.platform, "url": self.info.url,
                "title": self.info.title, "viewers": self.info.viewers,
                "since": self.started_at, "clips": self.clips_made, "pending": len(self.pending),
                "score": self.detector.last_score}

    def _on_segment(self, seg: Segment) -> None:
        for ts, db in loudness_series(seg.path, seg.start):
            self.detector.add_audio(ts, db)

    def run(self) -> None:
        log.info("watching %s on %s: %s", self.streamer.name, self.info.platform, self.info.url)
        self.recorder.start()
        if self.chat:
            self.chat.start()
        last_clip_poll = time.time()
        next_facecam = time.time() + 60
        try:
            while not self._halt.is_set():
                time.sleep(1)
                now = time.time()
                m = self.detector.tick(now)
                if m:
                    log.info("%s: moment detected (score %.1f, %s)", self.streamer.name, m.score, m.signals)
                    self.pending.append(m)
                self._cut_ready(force=self.recorder.finished.is_set())
                if self.twitch_api and self.info.platform == "twitch" and now - last_clip_poll > 45:
                    self._poll_viewer_clips(last_clip_poll - 15)
                    last_clip_poll = now
                if now >= next_facecam:
                    next_facecam = now + 300
                    self._refresh_facecam()
                if self.recorder.finished.is_set() and not self.pending:
                    break
                if now - self.recorder.last_segment_at > 120:
                    log.warning("%s: no video for 2 minutes, stopping worker", self.streamer.name)
                    break
        finally:
            self.recorder.stop()
            if self.chat:
                self.chat.stop()
            log.info("stopped watching %s (%d clips)", self.streamer.name, self.clips_made)

    def _refresh_facecam(self) -> None:
        """Looks for the webcam box over the buffered minutes of stream, in the background."""
        if self.streamer.facecam or self.cfg.edit.layout not in ("auto", "split"):
            return
        if self._facecam_thread and self._facecam_thread.is_alive():
            return

        def work():
            box = detect_from_segments([s.path for s in self.recorder.completed_segments()])
            if box:
                self.facecam, self._facecam_misses = box, 0
            else:
                self._facecam_misses += 1
                if self._facecam_misses >= 3:   # webcam gone for ~15 minutes
                    self.facecam = None
        self._facecam_thread = threading.Thread(target=work, daemon=True, name=f"facecam-{slug(self.streamer.name)}")
        self._facecam_thread.start()

    def _poll_viewer_clips(self, since: float) -> None:
        bid = self.info.extra.get("broadcaster_id")
        if not bid:
            return
        try:
            for c in self.twitch_api.recent_clips(bid, since):
                self.detector.add_viewer_clip(c["created_at"])
        except Exception as e:  # noqa: BLE001
            log.debug("viewer clip poll failed: %s", e)

    def _cut_ready(self, force: bool = False) -> None:
        segs = self.recorder.completed_segments() if not force else self.recorder.segments()
        covered_until = segs[-1].end if segs else 0
        for m in list(self.pending):
            if covered_until < m.end and not force:
                continue
            self.pending.remove(m)
            self._make_clip(m, segs)

    def _make_clip(self, m: Moment, segs: list[Segment]) -> None:
        clip_id = self.db.add_clip(streamer=self.streamer.name, platform=self.info.platform,
                                   channel=self.info.channel, stream_url=self.info.url,
                                   stream_title=self.info.title, moment_at=m.event_ts, score=m.score,
                                   signals=m.signals)
        out_dir = self.cfg.data_dir / "clips" / f"{clip_id:05d}_{slug(self.streamer.name)}"
        try:
            raw, offset, duration = cut_from_segments(segs, m.start, m.end, out_dir)
        except Exception as e:  # noqa: BLE001
            log.warning("cut failed for clip %s: %s", clip_id, e)
            self.db.update_clip(clip_id, status=dbm.FAILED, error=f"cut: {e}")
            return
        if duration < 5:
            self.db.update_clip(clip_id, status=dbm.FAILED, error="too little footage buffered")
            return
        self.db.update_clip(clip_id, raw_path=str(raw))
        self.clips_made += 1
        self.processor.submit(Job(clip_id, self.streamer, self.info, m, raw, offset, duration, facecam=self.facecam))
