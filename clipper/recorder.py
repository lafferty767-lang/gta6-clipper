"""Rolling buffer recorder: streamlink -> ffmpeg segmenter -> <buffer_dir>/<epoch>.ts

Each segment's filename is the wall-clock unix time it started, so chat/audio events
(also wall-clock) map straight onto video. Old segments are deleted after buffer_seconds.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class Segment:
    path: Path
    start: float
    end: float  # start of the next segment (or now for the newest)

    @property
    def duration(self) -> float:
        return self.end - self.start


class Recorder(threading.Thread):
    def __init__(self, url: str, buffer_dir: Path, quality: str = "720p60,720p,best",
                 segment_seconds: int = 4, buffer_seconds: int = 300,
                 on_segment: Callable[[Segment], None] | None = None):
        super().__init__(daemon=True, name=f"rec-{buffer_dir.name}")
        self.url = url
        self.buffer_dir = buffer_dir
        self.quality = quality
        self.segment_seconds = segment_seconds
        self.buffer_seconds = buffer_seconds
        self.on_segment = on_segment
        self._halt = threading.Event()
        self._procs: list[subprocess.Popen] = []
        self._seen: set[str] = set()
        self.last_segment_at = time.time()
        self.finished = threading.Event()  # set when the stream process exits
        self.buffer_dir.mkdir(parents=True, exist_ok=True)

    # --- public -------------------------------------------------------------------
    def stop(self) -> None:
        self._halt.set()
        for p in self._procs:
            if p.poll() is None:
                p.terminate()

    def segments(self) -> list[Segment]:
        files = sorted(self.buffer_dir.glob("*.ts"), key=lambda p: int(p.stem))
        segs = []
        for i, f in enumerate(files):
            start = float(f.stem)
            end = float(files[i + 1].stem) if i + 1 < len(files) else time.time()
            segs.append(Segment(f, start, end))
        return segs

    def completed_segments(self) -> list[Segment]:
        return self.segments()[:-1]  # newest one is still being written

    @property
    def is_local_file(self) -> bool:
        return os.path.exists(self.url)

    # --- thread -------------------------------------------------------------------
    def run(self) -> None:
        self._start_procs()
        try:
            while not self._halt.is_set():
                time.sleep(1)
                self._scan()
                if all(p.poll() is not None for p in self._procs):
                    log.info("recorder for %s exited (stream ended or dropped)", self.url)
                    self._scan(final=True)
                    break
        finally:
            self.stop()
            self.finished.set()

    def _start_procs(self) -> None:
        pattern = str(self.buffer_dir / "%s.ts")
        ff_out = ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "segment",
                  "-segment_time", str(self.segment_seconds), "-reset_timestamps", "1",
                  "-strftime", "1", pattern]
        if self.is_local_file:  # test mode: play a file in real time as if it were live
            ff = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-i", self.url, *ff_out])
            self._procs = [ff]
            return
        if not shutil.which("streamlink"):
            raise RuntimeError("streamlink is not installed")
        sl = subprocess.Popen(
            ["streamlink", "--stdout", "--loglevel", "warning", "--twitch-disable-ads",
             "--hls-live-edge", "2", "--stream-segment-threads", "2", self.url, self.quality],
            stdout=subprocess.PIPE)
        ff = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", *ff_out],
                              stdin=sl.stdout)
        assert sl.stdout is not None
        sl.stdout.close()  # let streamlink get SIGPIPE if ffmpeg dies
        self._procs = [sl, ff]

    def _scan(self, final: bool = False) -> None:
        segs = self.segments()
        done = segs if final else segs[:-1]
        for seg in done:
            if seg.path.name in self._seen:
                continue
            self._seen.add(seg.path.name)
            self.last_segment_at = time.time()
            if self.on_segment:
                try:
                    self.on_segment(seg)
                except Exception:  # noqa: BLE001
                    log.exception("on_segment failed")
        cutoff = time.time() - self.buffer_seconds
        for seg in segs[:-1]:
            if seg.end < cutoff:
                try:
                    seg.path.unlink()
                except FileNotFoundError:
                    pass
                self._seen.discard(seg.path.name)
