"""YouTube live detection without API quota: reads the channel's /live page.

When a channel is live, https://www.youtube.com/@handle/live serves the watch page of the
live video (canonical link = watch?v=...) with "isLiveNow":true in its microformat.
"""
from __future__ import annotations

import html
import logging
import re

import httpx

from .base import LiveInfo

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
CANON = re.compile(r'<link rel="canonical" href="([^"]+)"')
OG_TITLE = re.compile(r'<meta property="og:title" content="([^"]*)"')
GAME = re.compile(r'"richMetadataRenderer":\{"style":"RICH_METADATA_RENDERER_STYLE_BOX_ART".{0,400}?"title":\{"simpleText":"([^"]+)"')
VIEWERS = re.compile(r'"originalViewCount":"(\d+)"')


def _channel_url(handle: str) -> str:
    h = handle.strip()
    if h.startswith("http"):
        return h.rstrip("/")
    if h.startswith("UC") and len(h) == 24:
        return f"https://www.youtube.com/channel/{h}"
    return f"https://www.youtube.com/{h if h.startswith('@') else '@' + h}"


class YouTubeLive:
    def __init__(self):
        self._http = httpx.Client(timeout=20, follow_redirects=True, headers={
            "User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, cookies={"CONSENT": "YES+1", "SOCS": "CAI"})

    def live_status(self, handle: str) -> LiveInfo:
        base = _channel_url(handle)
        info = LiveInfo("youtube", handle, False, base + "/live")
        try:
            r = self._http.get(base + "/live")
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("youtube live check failed for %s: %s", handle, e)
            return info
        page = r.text
        canon = CANON.search(page)
        if not canon or "watch?v=" not in canon.group(1):
            return info
        if '"isLiveNow":true' not in page:
            return info  # an upcoming/premiere or a VOD
        video_url = canon.group(1)
        title = html.unescape(OG_TITLE.search(page).group(1)) if OG_TITLE.search(page) else ""
        game = GAME.search(page)
        viewers = VIEWERS.search(page)
        return LiveInfo("youtube", handle, True, video_url, title=title,
                        game=html.unescape(game.group(1)) if game else "",
                        viewers=int(viewers.group(1)) if viewers else 0,
                        extra={"video_id": video_url.split("v=")[-1].split("&")[0]})
