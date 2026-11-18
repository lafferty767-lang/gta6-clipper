"""Account connection flows started from the dashboard's Setup page.

YouTube uses Google's device flow: the dashboard shows a short code, you open google.com/device on
any phone or computer, type the code and approve. No redirect URL or local Python needed.

TikTok uses the normal web redirect flow back to https://<your server>/connect/tiktok/callback,
which is why the server needs its public HTTPS address (the installer sets that up).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

GOOGLE_DEVICE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# Google only allows a short list of scopes in the device flow; "youtube" (manage your YouTube
# account) is on it and covers uploading. youtube.upload on its own is not on the list.
YOUTUBE_DEVICE_SCOPES = ["https://www.googleapis.com/auth/youtube"]

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_SCOPES = "user.info.basic,video.upload,video.publish"


@dataclass
class DeviceFlow:
    user_code: str = ""
    verification_url: str = ""
    expires_at: float = 0.0
    status: str = "idle"   # idle | waiting | done | error
    message: str = ""
    started_at: float = field(default_factory=time.time)


class YouTubeDeviceConnector:
    """Runs one device flow at a time in a background thread."""

    def __init__(self, on_refresh_token):
        self.on_refresh_token = on_refresh_token
        self.flow = DeviceFlow()
        self._lock = threading.Lock()

    def start(self) -> DeviceFlow:
        cid, secret = os.environ.get("YOUTUBE_CLIENT_ID"), os.environ.get("YOUTUBE_CLIENT_SECRET")
        if not (cid and secret):
            self.flow = DeviceFlow(status="error", message="Save the YouTube OAuth client ID and secret first.")
            return self.flow
        r = httpx.post(GOOGLE_DEVICE_URL, data={"client_id": cid, "scope": " ".join(YOUTUBE_DEVICE_SCOPES)}, timeout=20)
        j = r.json()
        if r.status_code != 200 or "device_code" not in j:
            hint = ""
            if j.get("error") in ("invalid_client", "unauthorized_client"):
                hint = " Make sure the OAuth client type is 'TVs and Limited Input devices'."
            self.flow = DeviceFlow(status="error", message=f"Google said: {j.get('error_description') or j.get('error') or r.text[:200]}.{hint}")
            return self.flow
        with self._lock:
            self.flow = DeviceFlow(user_code=j["user_code"],
                                   verification_url=j.get("verification_url") or j.get("verification_uri") or "https://www.google.com/device",
                                   expires_at=time.time() + int(j.get("expires_in", 1800)), status="waiting")
            flow = self.flow
        threading.Thread(target=self._poll, args=(flow, j["device_code"], int(j.get("interval", 5)), cid, secret),
                         daemon=True, name="youtube-device-flow").start()
        return flow

    def _poll(self, flow: DeviceFlow, device_code: str, interval: int, cid: str, secret: str) -> None:
        while time.time() < flow.expires_at and flow is self.flow:
            time.sleep(interval)
            try:
                r = httpx.post(GOOGLE_TOKEN_URL, data={"client_id": cid, "client_secret": secret,
                                                       "device_code": device_code, "grant_type": DEVICE_GRANT}, timeout=20)
                j = r.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("youtube device poll failed: %s", e)
                continue
            err = j.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err:
                flow.status, flow.message = "error", {
                    "access_denied": "You pressed Deny on Google's page. Start again and press Allow.",
                    "expired_token": "The code expired. Start again.",
                }.get(err, f"Google said: {j.get('error_description') or err}")
                return
            if not j.get("refresh_token"):
                flow.status, flow.message = "error", "Google did not return a refresh token. Remove the app's access at myaccount.google.com/permissions and try again."
                return
            self.on_refresh_token(j["refresh_token"])
            flow.status, flow.message = "done", "YouTube connected."
            return
        if flow.status == "waiting":
            flow.status, flow.message = "error", "The code expired. Start again."


class TikTokConnector:
    def __init__(self, on_tokens):
        self.on_tokens = on_tokens
        self._states: dict[str, float] = {}

    @staticmethod
    def redirect_uri() -> str:
        base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
        return f"{base}/connect/tiktok/callback" if base else ""

    def authorize_url(self) -> str:
        key = os.environ.get("TIKTOK_CLIENT_KEY")
        if not key:
            raise RuntimeError("Save the TikTok client key and secret first.")
        if not self.redirect_uri().startswith("https://"):
            raise RuntimeError("PUBLIC_BASE_URL must be your server's https address (the installer sets it).")
        state = secrets.token_urlsafe(16)
        now = time.time()
        self._states = {s: t for s, t in self._states.items() if now - t < 900}
        self._states[state] = now
        return TIKTOK_AUTH_URL + "?" + urllib.parse.urlencode({
            "client_key": key, "scope": TIKTOK_SCOPES, "response_type": "code",
            "redirect_uri": self.redirect_uri(), "state": state})

    def finish(self, code: str, state: str) -> None:
        if state not in self._states:
            raise RuntimeError("This TikTok login link expired or didn't come from this dashboard. Start again.")
        self._states.pop(state, None)
        r = httpx.post(TIKTOK_TOKEN_URL, data={
            "client_key": os.environ.get("TIKTOK_CLIENT_KEY", ""), "client_secret": os.environ.get("TIKTOK_CLIENT_SECRET", ""),
            "code": code, "grant_type": "authorization_code", "redirect_uri": self.redirect_uri()},
            headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        j = r.json()
        if "refresh_token" not in j:
            raise RuntimeError(f"TikTok said: {j.get('error_description') or j.get('error') or json.dumps(j)[:200]}")
        self.on_tokens(j)
