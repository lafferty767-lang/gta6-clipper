"""Schedule clips through Metricool (posts to whatever networks your Metricool brand has connected).

Env: METRICOOL_USER_TOKEN, METRICOOL_USER_ID, METRICOOL_BLOG_ID, PUBLIC_BASE_URL
Metricool's API is on its Advanced/Custom plans. Find the token under Account settings -> API.

Metricool fetches the video from a public URL, so the dashboard serves each finished clip at
  {PUBLIC_BASE_URL}/public/<random-token>.mp4
PUBLIC_BASE_URL must be reachable from the internet (e.g. https://clips.yourdomain.com).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import Uploader

API = "https://app.metricool.com/api"


class MetricoolUploader(Uploader):
    name = "metricool"

    def configured(self):
        need = ["METRICOOL_USER_TOKEN", "METRICOOL_USER_ID", "METRICOOL_BLOG_ID"]
        missing = [k for k in need if not os.environ.get(k)]
        if not self.public_base_url:
            missing.append("PUBLIC_BASE_URL")
        return (not missing, f"missing env {', '.join(missing)}" if missing else "")

    def upload(self, clip: dict, video: Path) -> dict:
        headers = {"X-Mc-Auth": os.environ["METRICOOL_USER_TOKEN"], "Content-Type": "application/json"}
        params = {"userId": os.environ["METRICOOL_USER_ID"], "blogId": os.environ["METRICOOL_BLOG_ID"]}
        media_url = f"{self.public_base_url.rstrip('/')}/public/{clip['public_token']}.mp4"

        # Ask Metricool to copy the file to its own storage so the link can't expire.
        try:
            r = httpx.get(f"{API}/actions/normalize/image/url", params={**params, "url": media_url},
                          headers=headers, timeout=120)
            if r.status_code == 200 and r.text.strip().startswith("http"):
                media_url = r.text.strip().strip('"')
        except httpx.HTTPError:
            pass  # fall back to our own public URL + saveExternalMediaFiles

        delay = int(self.options.get("schedule_delay_minutes", 10))
        tz = self.options.get("timezone", "UTC")
        when = datetime.now(timezone.utc) + timedelta(minutes=delay)
        if tz != "UTC":
            from zoneinfo import ZoneInfo
            when = when.astimezone(ZoneInfo(tz))
        networks = self.options.get("networks", ["tiktok", "youtube"])
        text = f"{clip['title']}\n\n{clip['description']}"
        body = {
            "publicationDate": {"dateTime": when.strftime("%Y-%m-%dT%H:%M:%S"), "timezone": tz},
            "text": text[:2200],
            "providers": [{"network": n} for n in networks],
            "media": [media_url],
            "saveExternalMediaFiles": True,
            "autoPublish": not self.options.get("draft", False),
            "draft": bool(self.options.get("draft", False)),
            "shortener": False,
        }
        if "youtube" in networks:
            body["youtubeData"] = {"title": clip["title"][:100], "type": "SHORT",
                                   "privacy": self.options.get("youtube_privacy", "PUBLIC"), "madeForKids": False}
        body.update(self.options.get("extra_fields") or {})
        r = httpx.post(f"{API}/v2/scheduler/posts", params=params, headers=headers, json=body, timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f"Metricool scheduling failed: HTTP {r.status_code} {r.text[:300]}")
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        post_id = (data.get("data") or data).get("id") if isinstance(data, dict) else None
        return {"id": post_id, "url": "", "scheduled_for": body["publicationDate"]["dateTime"]}
