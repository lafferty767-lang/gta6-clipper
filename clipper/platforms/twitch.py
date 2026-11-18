"""Twitch Helix API: live status, viewer-made clips, and GTA 6 stream discovery.

Needs TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET (an app from https://dev.twitch.tv/console/apps).
Uses an app access token (client credentials), so no user login is needed.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import httpx

from .base import LiveInfo

log = logging.getLogger(__name__)
HELIX = "https://api.twitch.tv/helix"
GTA6_NAMES = ["Grand Theft Auto VI", "Grand Theft Auto 6", "GTA VI"]


class TwitchAPI:
    def __init__(self, client_id: str | None, client_secret: str | None):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._token_exp = 0.0
        self._lock = threading.Lock()
        self._game_id: str | None = None
        self._http = httpx.Client(timeout=15)

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _auth(self) -> dict[str, str]:
        with self._lock:
            if not self._token or time.time() > self._token_exp - 300:
                r = self._http.post("https://id.twitch.tv/oauth2/token", params={
                    "client_id": self.client_id, "client_secret": self.client_secret,
                    "grant_type": "client_credentials"})
                r.raise_for_status()
                j = r.json()
                self._token = j["access_token"]
                self._token_exp = time.time() + int(j.get("expires_in", 3600))
            return {"Client-Id": self.client_id or "", "Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params) -> dict:
        r = self._http.get(f"{HELIX}/{path}", params=params, headers=self._auth())
        if r.status_code == 401:  # token expired early
            self._token = None
            r = self._http.get(f"{HELIX}/{path}", params=params, headers=self._auth())
        r.raise_for_status()
        return r.json()

    def live_status(self, logins: list[str]) -> dict[str, LiveInfo]:
        """Returns {login_lower: LiveInfo} for the given logins (offline ones included)."""
        out = {l.lower(): LiveInfo("twitch", l, False, f"https://twitch.tv/{l}") for l in logins}
        if not self.configured or not logins:
            return out
        for i in range(0, len(logins), 100):
            batch = logins[i:i + 100]
            data = self._get("streams", [("user_login", l) for l in batch] + [("first", "100")])
            for s in data.get("data", []):
                login = s["user_login"].lower()
                out[login] = LiveInfo(
                    platform="twitch", channel=s["user_login"], is_live=s.get("type") == "live",
                    url=f"https://twitch.tv/{s['user_login']}", title=s.get("title", ""),
                    game=s.get("game_name", ""), viewers=int(s.get("viewer_count", 0)),
                    extra={"broadcaster_id": s["user_id"], "started_at": s.get("started_at")})
        return out

    def recent_clips(self, broadcaster_id: str, since: float) -> list[dict]:
        """Viewer-made clips created after `since` (unix ts)."""
        if not self.configured:
            return []
        started = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = self._get("clips", {"broadcaster_id": broadcaster_id, "started_at": started, "first": "50"})
        clips = []
        for c in data.get("data", []):
            try:
                created = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError):
                continue
            if created >= since:
                clips.append({"id": c["id"], "created_at": created, "title": c.get("title", ""),
                              "views": c.get("view_count", 0), "duration": c.get("duration", 30)})
        return clips

    def gta6_game_id(self) -> str | None:
        if self._game_id or not self.configured:
            return self._game_id
        for name in GTA6_NAMES:
            data = self._get("games", {"name": name})
            if data.get("data"):
                self._game_id = data["data"][0]["id"]
                break
        return self._game_id

    def top_gta6_streams(self, languages: list[str] | None = None, top: int = 25) -> list[LiveInfo]:
        gid = self.gta6_game_id()
        if not gid:
            return []
        params: list[tuple[str, str]] = [("game_id", gid), ("first", str(min(top, 100)))]
        for lang in languages or []:
            params.append(("language", lang))
        data = self._get("streams", params)
        return [LiveInfo("twitch", s["user_login"], True, f"https://twitch.tv/{s['user_login']}",
                         title=s.get("title", ""), game=s.get("game_name", ""),
                         viewers=int(s.get("viewer_count", 0)),
                         extra={"display_name": s.get("user_name"), "language": s.get("language", "")})
                for s in data.get("data", [])]
