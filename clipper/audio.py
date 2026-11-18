"""Audio loudness per short window, decoded with ffmpeg into numpy."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

SR = 16000


def loudness_series(path: Path, start_ts: float, window: float = 0.5) -> list[tuple[float, float]]:
    """Returns [(wall_clock_ts, rms_dbfs), ...] for each `window` seconds of the file's audio."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(SR), "-f", "s16le", "pipe:1"],
        capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout:
        return []
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(SR * window)
    out = []
    for i in range(0, len(pcm) - n + 1, n):
        chunk = pcm[i:i + n]
        rms = float(np.sqrt(np.mean(chunk * chunk)) + 1e-9)
        out.append((start_ts + i / SR, 20 * np.log10(rms)))
    return out
