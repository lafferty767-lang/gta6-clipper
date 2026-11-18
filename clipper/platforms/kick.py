"""Kick live detection via its public channel endpoint (unofficial; may change).

Kick sits behind Cloudflare, so we use curl_cffi's browser impersonation when available.
"""
from __future__ import annotations

import logging

from .base import LiveInfo

log = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cffi_requests  # type: ignore
except ImportError:  # pragma: no cover
    cffi_requests = None


class KickLive:
    def _get_json(self, url: str) -> dict | None:
        try:
            if cffi_requests is not None:
                r = cffi_requests.get(url, impersonate="chrome", timeout=20)
            else:
                import httpx
                r = httpx.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            if r.status_code != 200:
                log.warning("kick %s -> HTTP %s", url, r.status_code)
                return None
            return r.json()
        except Exception as e:  # noqa: BLE001 - network/JSON errors are all "unknown"
            log.warning("kick request failed for %s: %s", url, e)
            return None

    def live_status(self, slug: str) -> LiveInfo:
        info = LiveInfo("kick", slug, False, f"https://kick.com/{slug}")
        data = self._get_json(f"https://kick.com/api/v2/channels/{slug}")
        if not data:
            return info
        ls = data.get("livestream") or {}
        chatroom_id = (data.get("chatroom") or {}).get("id")
        if not ls or not ls.get("is_live"):
            info.extra["chatroom_id"] = chatroom_id
            return info
        cats = ls.get("categories") or []
        game = cats[0].get("name", "") if cats else ""
        return LiveInfo("kick", slug, True, f"https://kick.com/{slug}", title=ls.get("session_title", ""),
                        game=game, viewers=int(ls.get("viewer_count") or ls.get("viewers") or 0),
                        extra={"chatroom_id": chatroom_id, "channel_id": data.get("id")})
