"""Cleans clips that were rendered before captions hid swear words.

The original stream footage is gone once a clip is rendered, so the finished video is fixed in
place: listen to it again, find the caption lines that held a swear or slur, blur the caption strip
for just those moments and write the same words back on top with the bad word as asterisks.
A marker file next to the video records that it's been checked, so each clip is done once.
Titles, descriptions and hashtags are cleaned at the same time.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from . import clean
from . import db as dbm
from .config import Config, Edit
from .editor import BLUR_FG_HEIGHT, build_ass, chunk_words, has_audio
from .transcribe import Word, extract_wav, transcribe

log = logging.getLogger(__name__)

MARK = "captions-clean-v1"
_LOCK = threading.Lock()
BAND = 260                       # height of the caption strip that gets covered (two lines of text)


def is_checked(final: Path) -> bool:
    return (final.parent / MARK).exists()


def mark_checked(final: Path) -> None:
    (final.parent / MARK).write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"))


def bad_chunks(words: list[Word]) -> list[list[Word]]:
    return [ch for ch in chunk_words(words) if any(not clean.is_clean(w.text) for w in ch)]


def windows(chunks: list[list[Word]], duration: float, pad_before: float = 0.3, pad_after: float = 0.45):
    """Merged (start, end) times covering every bad caption line, with some slack for timing drift."""
    spans = sorted((max(0.0, ch[0].start - pad_before), min(duration, max(ch[-1].end, ch[-1].start + 0.25) + pad_after))
                   for ch in chunks)
    out: list[list[float]] = []
    for s, e in spans:
        if out and s <= out[-1][1] + 0.1:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _gray_frame(path: Path, t: float, cfg: Edit):
    import numpy as np
    w, h = cfg.width, cfg.height
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
                          "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                         capture_output=True, timeout=60).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w) if len(raw) == w * h else None


def _caption_ink(g, y: int) -> float:
    """Share of white pixels hugging a thick black outline (how the captions are drawn) near y."""
    import cv2
    import numpy as np
    band = g[max(0, y - 70):y + 70]
    dark = cv2.dilate((band < 40).astype(np.uint8), np.ones((9, 9), np.uint8))
    return float(((band > 220) & (dark > 0)).mean())


def caption_y(path: Path, duration: float, cfg: Edit, times: list[float] = ()) -> int:
    """Where the captions sit: on the seam under the webcam (split layout) or under the picture
    (blur layout). Looks for caption lettering at both spots while words are on screen; if that
    doesn't settle it, the split layout is the one with a sharp webcam picture at the top, the
    blur layout has a heavily blurred backdrop there."""
    import cv2
    import numpy as np
    split_y, blur_y = cfg.facecam_height, (cfg.height + BLUR_FG_HEIGHT) // 2 + 170
    frames = [f for f in (_gray_frame(path, t, cfg) for t in (list(times)[:4] or [duration * 0.5])) if f is not None]
    if not frames:
        return split_y
    ink_split = sum(_caption_ink(f, split_y) for f in frames)
    ink_blur = sum(_caption_ink(f, blur_y) for f in frames)
    if max(ink_split, ink_blur) > 0.01 and max(ink_split, ink_blur) > 2 * min(ink_split, ink_blur):
        return split_y if ink_split > ink_blur else blur_y
    detail = max(float(cv2.Laplacian(f[40:360].astype(np.float64), cv2.CV_64F).var()) for f in frames)
    return split_y if detail > 6 else blur_y


def clean_video(final: Path, cfg: Config, language: str | None = None) -> str:
    """'clean' (nothing to hide), 'fixed' (swears covered), or 'no speech'. Raises if ffmpeg fails."""
    work = final.parent
    dur = _duration(final)
    wav = extract_wav(final, 0, dur, work / "recheck.wav")
    try:
        _text, words = transcribe(wav, cfg.whisper, language)
    finally:
        wav.unlink(missing_ok=True)
    if not words:
        return "no speech"
    chunks = bad_chunks(words)
    if not chunks:
        return "clean"
    e = cfg.edit
    y = caption_y(final, dur, e, [(w.start + w.end) / 2 for ch in chunks for w in ch])
    y0 = max(0, min(e.height - BAND, y - BAND // 2))
    spans = windows(chunks, dur)
    enable = "+".join(f"between(t,{s:.2f},{t:.2f})" for s, t in spans)
    (work / "recaption.ass").write_text(build_ass([w for ch in chunks for w in ch], e, y), encoding="utf-8")
    graph = (f"[0:v]split=2[a][b];[b]crop={e.width}:{BAND}:0:{y0},boxblur=28:6,eq=brightness=-0.15[bl];"
             f"[a][bl]overlay=0:{y0}:enable='{enable}'[c];[c]subtitles=recaption.ass[v]")
    tmp = work / "final.cleaning.mp4"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(final.resolve()),
           "-filter_complex", graph, "-map", "[v]"]
    if has_audio(final):
        cmd += ["-map", "0:a:0", "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-preset", e.preset, "-crf", str(e.crf), "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-movflags", "+faststart", str(tmp.resolve())]
    try:
        subprocess.run(cmd, check=True, cwd=work, timeout=1800)
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
        (work / "recaption.ass").unlink(missing_ok=True)
    log.info("covered %d swear caption line(s) in %s", len(chunks), final)
    return "fixed"


def clean_text(db: dbm.DB, clip: dict) -> bool:
    """Cleans a clip's title, description and hashtags in the database. True if anything changed."""
    title = clean.safe_title(clip.get("title") or "", clip["streamer"]) if clip.get("title") else clip.get("title")
    desc = clean.scrub(clip.get("description") or "") if clip.get("description") else clip.get("description")
    tags = [h for h in (clip.get("hashtags") or []) if clean.is_clean(str(h).lstrip("#"))]
    if (title, desc, tags) == (clip.get("title"), clip.get("description"), clip.get("hashtags") or []):
        return False
    db.update_clip(clip["id"], title=title, description=desc, hashtags=tags)
    return True


def check_clip(db: dbm.DB, cfg: Config, clip: dict, language: str | None = None) -> str:
    """Cleans one clip's text and video if it hasn't been checked yet."""
    clean_text(db, clip)
    final = Path(clip.get("final_path") or "")
    if not clip.get("final_path") or not final.exists():
        return "no video"
    with _LOCK:          # the uploader and the background cleaner may reach the same clip
        if is_checked(final):
            return "already checked"
        result = clean_video(final, cfg, language)
        mark_checked(final)
        return result


# clips not posted yet, the ones next in line first
ORDER = [dbm.APPROVED, dbm.READY, dbm.NEEDS_PERMISSION, dbm.LOW_SCORE]


class CaptionCleaner(threading.Thread):
    """Works through older clips in the background, one at a time, when nothing else is rendering."""

    def __init__(self, orch):
        super().__init__(daemon=True, name="caption-cleaner")
        self.orch = orch

    def todo(self) -> list[dict]:
        out = []
        for status in ORDER:
            out += [c for c in self.orch.db.list_clips([status], 2000)
                    if c.get("final_path") and Path(c["final_path"]).exists() and not is_checked(Path(c["final_path"]))]
        return out

    def run(self) -> None:
        time.sleep(60)
        db = self.orch.db
        for c in db.list_clips(None, 5000):          # text first: quick, and covers every clip
            try:
                clean_text(db, c)
            except Exception:  # noqa: BLE001
                log.exception("cleaning the text of clip %s failed", c.get("id"))
        while True:
            try:
                if not self.orch.processor.q.empty():
                    time.sleep(30)
                    continue
                todo = self.todo()
                if not todo:
                    time.sleep(600)
                    continue
                c = db.get_clip(todo[0]["id"])
                s = self.orch.streamer_by_name(c["streamer"])
                res = check_clip(db, self.orch.cfg, c, s.language if s else None)
                log.info("caption check of clip %s: %s (%d left)", c["id"], res, len(todo) - 1)
            except Exception:  # noqa: BLE001
                log.exception("caption check failed")
                time.sleep(120)
