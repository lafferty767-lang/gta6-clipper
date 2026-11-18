"""Keys and tokens entered in the dashboard's Setup page.

Stored in <data_dir>/secrets.json (file mode 600) and applied on top of the process environment,
so values typed in the dashboard win over .env. Everything else in the code keeps reading
os.environ, which keeps the uploaders and platform clients simple.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    secret: bool = True
    help: str = ""


# Grouped for the Setup page. Order matters (it's the order shown).
GROUPS: list[tuple[str, str, list[Field]]] = [
    ("twitch", "Twitch (finds live streams, viewer clips, suggestions)", [
        Field("TWITCH_CLIENT_ID", "Client ID", secret=False,
              help="dev.twitch.tv/console/apps → Register Your Application → Manage"),
        Field("TWITCH_CLIENT_SECRET", "Client Secret", help="Same page → New Secret"),
    ]),
    ("anthropic", "Claude (AI picks the best clips and writes titles)", [
        Field("ANTHROPIC_API_KEY", "API key", help="console.anthropic.com → API Keys → Create Key"),
    ]),
    ("youtube", "YouTube Shorts", [
        Field("YOUTUBE_CLIENT_ID", "OAuth client ID", secret=False,
              help="Google Cloud → Credentials → OAuth client ID (type: TVs and Limited Input devices)"),
        Field("YOUTUBE_CLIENT_SECRET", "OAuth client secret"),
        Field("YOUTUBE_REFRESH_TOKEN", "Refresh token", help="Filled in for you by the Connect YouTube button"),
    ]),
    ("tiktok", "TikTok", [
        Field("TIKTOK_CLIENT_KEY", "Client key", secret=False, help="developers.tiktok.com → your app"),
        Field("TIKTOK_CLIENT_SECRET", "Client secret"),
        Field("TIKTOK_REFRESH_TOKEN", "Refresh token", help="Filled in for you by the Connect TikTok button"),
    ]),
    ("metricool", "Metricool (optional scheduler)", [
        Field("METRICOOL_USER_TOKEN", "API token", help="Metricool → Account settings → API"),
        Field("METRICOOL_USER_ID", "User ID", secret=False, help="userId=… in the Metricool page address"),
        Field("METRICOOL_BLOG_ID", "Brand (blog) ID", secret=False, help="blogId=… in the Metricool page address"),
    ]),
]
ALL_KEYS = {f.key for _, _, fields in GROUPS for f in fields}
# Saved like the keys above but not shown as a field (set by the dashboard's "use this address" button).
EXTRA_KEYS = {"PUBLIC_BASE_URL"}


class Settings:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "secrets.json"
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                self._values = {k: str(v) for k, v in json.loads(self.path.read_text()).items() if v}
            except (FileNotFoundError, ValueError):
                self._values = {}
            for k, v in self._values.items():
                os.environ[k] = v

    def get(self, key: str) -> str:
        return os.environ.get(key, "")

    def update(self, values: dict[str, str]) -> list[str]:
        """Saves non-empty values (empty = keep the current one). Returns the keys that changed."""
        changed = []
        with self._lock:
            for k, v in values.items():
                v = (v or "").strip()
                if k not in ALL_KEYS | EXTRA_KEYS or not v or os.environ.get(k) == v:
                    continue
                self._values[k] = v
                os.environ[k] = v
                changed.append(k)
            if changed:
                self._write()
        return changed

    def clear(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)
            os.environ.pop(key, None)
            self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._values, f, indent=2)
        os.replace(tmp, self.path)


def masked(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 12 else "set"
