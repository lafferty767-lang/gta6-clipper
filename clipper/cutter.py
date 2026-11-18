"""Turns a [start, end] wall-clock window into a raw clip file from the rolling buffer."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .recorder import Segment


def cut_from_segments(segments: list[Segment], start: float, end: float, out_dir: Path) -> tuple[Path, float, float]:
    """Copies the overlapping segments into out_dir and concatenates them (no re-encode).

    Returns (raw_path, offset_seconds_into_raw, duration_seconds).
    """
    picked = [s for s in segments if s.end > start and s.start < end]
    if not picked:
        raise RuntimeError("no buffered video covers this moment (buffer too short or stream dropped)")
    out_dir.mkdir(parents=True, exist_ok=True)
    parts_dir = out_dir / "parts"
    parts_dir.mkdir(exist_ok=True)
    listing = []
    for s in picked:
        dst = parts_dir / s.path.name
        shutil.copy2(s.path, dst)
        listing.append(f"file '{dst.resolve()}'")
    lst = out_dir / "parts.txt"
    lst.write_text("\n".join(listing) + "\n")
    raw = out_dir / "raw.ts"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(raw)], check=True)
    shutil.rmtree(parts_dir, ignore_errors=True)
    lst.unlink(missing_ok=True)
    offset = max(0.0, start - picked[0].start)
    available = picked[-1].end - picked[0].start
    duration = min(end - start, available - offset)
    return raw, offset, duration
