"""TikTok via the official Content Posting API.

Env: TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REFRESH_TOKEN
Get the refresh token with the Connect TikTok button on the dashboard's Setup page
(or, without the dashboard: python -m clipper auth-tiktok).

Modes (uploads.targets.tiktok.mode):
  inbox  - (default) sends the video to your TikTok inbox/drafts; you tap Post in the app.
           Works before TikTok has audited your developer app.
  direct - publishes straight to the profile. Until TikTok audits your app, direct posts
           are private (SELF_ONLY) and the TikTok account itself must be set to private, so use
           inbox until the audit is approved.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from . import Uploader

API = "https://open.tiktokapis.com/v2"
MB = 1024 * 1024


class TikTokUploader(Uploader):
    name = "tiktok"

    def configured(self):
        missing = [k for k in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET") if not os.environ.get(k)]
        if not (os.environ.get("TIKTOK_REFRESH_TOKEN") or self.db.kv_get("tiktok_token")):
            missing.append("TIKTOK_REFRESH_TOKEN")
        return (not missing, f"missing env {', '.join(missing)}" if missing else "")

    # --- auth ---------------------------------------------------------------------
    def _access_token(self) -> str:
        stored = json.loads(self.db.kv_get("tiktok_token") or "{}")
        if stored.get("access_token") and stored.get("expires_at", 0) > time.time() + 300:
            return stored["access_token"]
        refresh = stored.get("refresh_token") or os.environ["TIKTOK_REFRESH_TOKEN"]
        r = httpx.post(f"{API}/oauth/token/", data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"], "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "grant_type": "refresh_token", "refresh_token": refresh},
            headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        j = r.json()
        if "access_token" not in j:
            raise RuntimeError(f"TikTok token refresh failed: {j}")
        self.db.kv_set("tiktok_token", json.dumps({
            "access_token": j["access_token"], "refresh_token": j.get("refresh_token", refresh),
            "expires_at": time.time() + int(j.get("expires_in", 86400))}))
        return j["access_token"]

    def _post(self, path: str, token: str, body: dict) -> dict:
        r = httpx.post(f"{API}{path}", json=body, timeout=60, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
        j = r.json()
        err = (j.get("error") or {})
        if r.status_code != 200 or err.get("code") not in (None, "ok"):
            raise RuntimeError(f"TikTok {path} failed: HTTP {r.status_code} {err}")
        return j.get("data") or {}

    # --- upload -------------------------------------------------------------------
    def upload(self, clip: dict, video: Path) -> dict:
        token = self._access_token()
        size = video.stat().st_size
        if size <= 64 * MB:
            chunk, count = size, 1
        else:
            chunk = 10 * MB
            count = size // chunk  # TikTok: the last chunk absorbs the remainder
        source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        mode = self.options.get("mode", "inbox")

        if mode == "direct":
            info = self._post("/post/publish/creator_info/query/", token, {})
            allowed = info.get("privacy_level_options") or ["SELF_ONLY"]
            privacy = self.options.get("privacy", "PUBLIC_TO_EVERYONE")
            if privacy not in allowed:
                privacy = "SELF_ONLY" if "SELF_ONLY" in allowed else allowed[0]
            caption = f"{clip['title']} {' '.join(clip.get('hashtags') or [])}"[:2200]
            data = self._post("/post/publish/video/init/", token, {
                "post_info": {"title": caption, "privacy_level": privacy, "disable_duet": False,
                              "disable_comment": False, "disable_stitch": False,
                              "video_cover_timestamp_ms": 1000},
                "source_info": source})
        else:
            data = self._post("/post/publish/inbox/video/init/", token, {"source_info": source})

        upload_url, publish_id = data["upload_url"], data["publish_id"]
        with open(video, "rb") as f:
            for i in range(count):
                start = i * chunk
                end = size - 1 if i == count - 1 else start + chunk - 1
                f.seek(start)
                payload = f.read(end - start + 1)
                r = httpx.put(upload_url, content=payload, timeout=300, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(payload)),
                    "Content-Range": f"bytes {start}-{end}/{size}"})
                if r.status_code not in (200, 201, 206):
                    raise RuntimeError(f"TikTok chunk upload failed: HTTP {r.status_code} {r.text[:200]}")

        status = self._wait(token, publish_id)
        return {"id": publish_id, "url": "", "mode": mode, "status": status}

    def _wait(self, token: str, publish_id: str, timeout: float = 300) -> str:
        deadline = time.time() + timeout
        status = "PROCESSING_UPLOAD"
        while time.time() < deadline:
            data = self._post("/post/publish/status/fetch/", token, {"publish_id": publish_id})
            status = data.get("status", status)
            if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                return status
            if status == "FAILED":
                raise RuntimeError(f"TikTok processing failed: {data.get('fail_reason')}")
            time.sleep(5)
        return status


def run_auth_flow() -> None:
    """Interactive: prints the authorize URL, then exchanges the redirected code for tokens."""
    import secrets
    import urllib.parse

    key = os.environ.get("TIKTOK_CLIENT_KEY") or input("TikTok client key: ").strip()
    secret = os.environ.get("TIKTOK_CLIENT_SECRET") or input("TikTok client secret: ").strip()
    redirect = os.environ.get("TIKTOK_REDIRECT_URI") or input(
        "Redirect URI registered in your TikTok app (the dashboard's Connect TikTok button is easier): ").strip()
    state = secrets.token_urlsafe(8)
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": key, "scope": "user.info.basic,video.upload,video.publish",
        "response_type": "code", "redirect_uri": redirect, "state": state})
    print(f"\n1) Open this URL, log in with the TikTok account you want to post to, and approve:\n\n{url}\n")
    back = input("2) Paste the full URL you were redirected to: ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(back).query).get("code", [back])[0]
    r = httpx.post(f"{API}/oauth/token/", data={
        "client_key": key, "client_secret": secret, "code": code, "grant_type": "authorization_code",
        "redirect_uri": redirect}, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    j = r.json()
    if "refresh_token" not in j:
        raise SystemExit(f"Token exchange failed: {j}")
    print("\nAdd this to your .env:\n")
    print(f"TIKTOK_REFRESH_TOKEN={j['refresh_token']}")
