"""Finds the streamer's webcam box in a gameplay stream, so clips can put the streamer on top.

Webcam overlays are rectangles where the picture barely changes (a room behind a person) on top
of gameplay that changes all the time. So: sample frames across the clip, measure how much each
pixel changes, and look for a calm rectangle of webcam-like size. A face found inside it confirms it.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import Facecam

log = logging.getLogger(__name__)

SW, SH = 320, 180          # analysis size
CALM = 14.0                # per-pixel brightness std (0..255) below which a pixel counts as calm


def sample_frames(path: Path, offset: float, duration: float, n: int = 24):
    """n frames spread over the clip: (640x360 colour frames, n x SH x SW grayscale stack)."""
    import cv2
    import numpy as np
    fw, fh = 640, 360
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{offset:.3f}", "-t", f"{duration:.3f}",
         "-i", str(path), "-vf", f"fps={n / max(duration, 1):.4f},scale={fw}:{fh}", "-pix_fmt", "bgr24",
         "-f", "rawvideo", "-"], capture_output=True, check=True, timeout=120).stdout
    count = len(out) // (fw * fh * 3)
    frames = [np.frombuffer(out, np.uint8, fw * fh * 3, i * fw * fh * 3).reshape(fh, fw, 3) for i in range(count)]
    small = [cv2.cvtColor(cv2.resize(f, (SW, SH), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
             for f in frames]
    return frames, (np.stack(small).astype(np.float32) if small else None)


MODEL = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"


def _faces(frames) -> list[tuple[float, float, float, float]]:
    """Face boxes (as fractions of the frame) found in the sampled frames."""
    import cv2
    if not frames:
        return []
    h, w = frames[0].shape[:2]
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), 0.7)
    out = []
    for fr in frames:
        _, faces = det.detect(fr)
        for f in faces if faces is not None else []:
            x, y, fw, fh = (float(v) for v in f[:4])
            out.append((x / w, y / h, fw / w, fh / h))
    return out


def find_box(stack, faces=()) -> Facecam | None:
    """stack: frames x SH x SW grayscale. Returns the webcam box, or None if there isn't a clear one."""
    import cv2
    import numpy as np
    if stack is None or len(stack) < 6:
        return None
    std = stack.std(axis=0)
    if (std < CALM).mean() > 0.6:      # the whole picture sat still (menu, loading screen): can't tell
        return _around_steady_face(faces, len(stack))
    calm = (std < CALM).astype(np.uint8)
    calm = cv2.morphologyEx(calm, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    calm = cv2.morphologyEx(calm, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(calm, 8)
    mean = stack.mean(axis=0)
    best, best_score = None, 0.0
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        frac = (w * h) / (SW * SH)
        if not (0.015 <= frac <= 0.3) or not (0.8 <= w / h <= 2.6):
            continue
        fill = area / (w * h)
        if fill < 0.45:
            continue
        inner_std = std[y:y + h, x:x + w]
        moving = (inner_std > CALM * 1.5).mean()          # the person moves a bit; a static panel doesn't
        detail = mean[y:y + h, x:x + w].std()              # a room has detail; a black bar doesn't
        if detail < 12 or moving < 0.02:
            continue
        fx = [f for f in faces if x / SW <= f[0] + f[2] / 2 <= (x + w) / SW and y / SH <= f[1] + f[3] / 2 <= (y + h) / SH]
        if not fx and fill < 0.6:
            continue
        score = fill * (1 + min(moving, 0.4)) * (3 if fx else 1) * min(frac / 0.05, 1.5)
        if score > best_score:
            best, best_score = (x, y, w, h, bool(fx)), score
    if not best:
        return _around_steady_face(faces, len(stack))
    x, y, w, h, has_face = best
    # trim a pixel of border blur on each side
    x, y, w, h = x + 1, y + 1, max(w - 2, 4), max(h - 2, 4)
    log.info("webcam box found at %d,%d %dx%d of %dx%d (face %s)", x, y, w, h, SW, SH, "yes" if has_face else "no")
    return Facecam(x=float(x / SW), y=float(y / SH), w=float(w / SW), h=float(h / SH))


def frame_at(path: Path, t: float = 0.5):
    """One 640x360 colour frame from a stream segment."""
    import numpy as np
    out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
                          "-frames:v", "1", "-vf", "scale=640:360", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"],
                         capture_output=True, timeout=30).stdout
    return np.frombuffer(out, np.uint8).reshape(360, 640, 3) if len(out) == 640 * 360 * 3 else None


def _around_steady_face(faces, n_frames: int, aspect: float = 1.125 * 9 / 16) -> Facecam | None:
    """No clear webcam box, but a face that stays put in most frames: frame the area around it.
    aspect is the crop's width/height in frame fractions (1080x960 top half from a 16:9 frame)."""
    if not faces:
        return None
    best = []
    for f in faces:
        cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
        near = [g for g in faces if abs(g[0] + g[2] / 2 - cx) < 0.03 and abs(g[1] + g[3] / 2 - cy) < 0.05]
        if len(near) > len(best):
            best = near
    if len(best) < max(4, n_frames * 0.4):
        return None
    cx = sum(g[0] + g[2] / 2 for g in best) / len(best)
    cy = sum(g[1] + g[3] / 2 for g in best) / len(best)
    fh = sorted(g[3] for g in best)[len(best) // 2]
    h = min(fh * 3.2, 1.0)
    w = min(h * aspect, 1.0)
    x = min(max(cx - w / 2, 0.0), 1 - w)
    y = min(max(cy - h * 0.45, 0.0), 1 - h)
    log.info("no webcam box; framing the steady face at %.2f,%.2f", cx, cy)
    return Facecam(x=float(x), y=float(y), w=float(w), h=float(h))


def detect_from_segments(paths: list[Path], n: int = 36) -> Facecam | None:
    """Webcam box from one frame of each of up to n stream segments (a few minutes of stream)."""
    import cv2
    import numpy as np
    try:
        pick = paths[:: max(1, len(paths) // n)][:n]
        frames = [f for f in (frame_at(p) for p in pick) if f is not None]
        if len(frames) < 8:
            return None
        stack = np.stack([cv2.cvtColor(cv2.resize(f, (SW, SH), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                          for f in frames]).astype(np.float32)
        return find_box(stack, _faces(frames))
    except Exception as e:  # noqa: BLE001 - layout falls back to the blurred one
        log.warning("webcam detection failed: %s", e)
        return None


def detect_facecam(path: Path, offset: float, duration: float) -> Facecam | None:
    try:
        frames, stack = sample_frames(path, offset, duration)
        return find_box(stack, _faces(frames) if frames else [])
    except Exception as e:  # noqa: BLE001 - layout falls back to the blurred one
        log.warning("webcam detection failed: %s", e)
        return None
