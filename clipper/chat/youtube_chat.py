"""YouTube live chat via the chat-downloader library (no API key, no quota)."""
from __future__ import annotations

import time

from . import ChatSource


class YouTubeChat(ChatSource):
    name_prefix = "youtube-chat"

    def __init__(self, channel, on_message, video_url: str | None):
        super().__init__(channel, on_message)
        self.video_url = video_url

    def _run_once(self) -> None:
        from chat_downloader import ChatDownloader  # imported lazily (heavy)

        if not self.video_url:
            raise RuntimeError("no live video url")
        chat = ChatDownloader().get_chat(self.video_url, message_types=["text_message", "paid_message"])
        for m in chat:
            if self.stopped:
                return
            self.on_message(time.time(), m.get("message") or "")
