"""YouTube Shorts via the YouTube Data API v3 (videos.insert).

Env: YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
Connect from the dashboard's Setup page (or run: python -m clipper auth-youtube).
A vertical video of 3 minutes or less is published as a Short automatically.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import Uploader


class YouTubeUploader(Uploader):
    name = "youtube"

    def configured(self):
        missing = [k for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN") if not os.environ.get(k)]
        return (not missing, f"missing env {', '.join(missing)}" if missing else "")

    def _service(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(None, refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
                            token_uri="https://oauth2.googleapis.com/token",
                            client_id=os.environ["YOUTUBE_CLIENT_ID"],
                            client_secret=os.environ["YOUTUBE_CLIENT_SECRET"])
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    def upload(self, clip: dict, video: Path) -> dict:
        from googleapiclient.http import MediaFileUpload

        tags = [h.lstrip("#") for h in (clip.get("hashtags") or [])]
        title = clip["title"]
        if "#shorts" not in title.lower() and len(title) < 90:
            title = f"{title} #shorts"
        body = {
            "snippet": {"title": title[:100], "description": clip["description"][:4900],
                        "tags": tags, "categoryId": str(self.options.get("category_id", "20"))},
            "status": {"privacyStatus": self.options.get("privacy", "public"),
                       "selfDeclaredMadeForKids": False},
        }
        req = self._service().videos().insert(
            part="snippet,status", body=body,
            media_body=MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True))
        resp = None
        while resp is None:
            _status, resp = req.next_chunk()
        vid = resp["id"]
        return {"id": vid, "url": f"https://youtube.com/shorts/{vid}"}


def run_auth_flow() -> None:
    """Terminal version of the dashboard's Connect YouTube button (Google device flow)."""
    import time

    from ..connect import YouTubeDeviceConnector

    got: dict[str, str] = {}
    conn = YouTubeDeviceConnector(lambda tok: got.setdefault("token", tok))
    flow = conn.start()
    if flow.status == "error":
        raise SystemExit(flow.message)
    print(f"\nOn any phone or computer open {flow.verification_url} and enter the code:  {flow.user_code}\n")
    while flow.status == "waiting":
        time.sleep(2)
    if flow.status != "done":
        raise SystemExit(flow.message)
    print("Connected. Add this line to .env (or paste it on the dashboard's Setup page):\n")
    print(f"YOUTUBE_REFRESH_TOKEN={got['token']}")
