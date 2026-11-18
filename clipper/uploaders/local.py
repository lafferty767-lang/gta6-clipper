"""'Upload' by copying the finished clip to a folder (handy for testing or manual posting)."""
from __future__ import annotations

import shutil
from pathlib import Path

from . import Uploader


class LocalUploader(Uploader):
    name = "local"

    def upload(self, clip: dict, video: Path) -> dict:
        dest = Path(self.options.get("folder", "/data/exports"))
        dest.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in clip["title"] if c.isalnum() or c in " -_")[:60].strip() or "clip"
        out = dest / f"{clip['id']:05d} {safe}.mp4"
        shutil.copy2(video, out)
        (out.with_suffix(".txt")).write_text(f"{clip['title']}\n\n{clip['description']}\n", encoding="utf-8")
        return {"id": out.name, "url": str(out)}
