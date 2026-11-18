"""Pluggable uploaders. Each target in config `uploads.targets` maps to one class here.

To add a platform: subclass Uploader, implement upload(), and register it in REGISTRY.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class Uploader:
    name = "base"

    def __init__(self, options: dict[str, Any], db, public_base_url: str | None = None):
        self.options = options
        self.db = db
        self.public_base_url = public_base_url

    def configured(self) -> tuple[bool, str]:
        """(ok, reason) - whether required credentials are present."""
        return True, ""

    def upload(self, clip: dict, video: Path) -> dict:
        """Upload and return {"id": ..., "url": ...}. Raise on failure."""
        raise NotImplementedError


def build_uploaders(targets: dict[str, dict], db, public_base_url: str | None) -> list[Uploader]:
    from .local import LocalUploader
    from .metricool import MetricoolUploader
    from .tiktok import TikTokUploader
    from .youtube import YouTubeUploader

    registry = {"youtube": YouTubeUploader, "tiktok": TikTokUploader,
                "metricool": MetricoolUploader, "local": LocalUploader}
    out = []
    for name, opts in (targets or {}).items():
        opts = opts or {}
        if not opts.get("enabled", False):
            continue
        cls = registry.get(name)
        if not cls:
            log.warning("unknown upload target %r (known: %s)", name, ", ".join(registry))
            continue
        up = cls(opts, db, public_base_url)
        ok, why = up.configured()
        if not ok:
            log.warning("upload target %s disabled: %s", name, why)
            continue
        out.append(up)
    return out
