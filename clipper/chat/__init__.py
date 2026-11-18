"""Live chat readers. Each runs in a background thread and calls on_message(ts, text)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

OnMessage = Callable[[float, str], None]


class ChatSource(threading.Thread):
    """Base class: subclasses implement _run_once(); we reconnect with backoff until stopped."""

    name_prefix = "chat"

    def __init__(self, channel: str, on_message: OnMessage):
        super().__init__(daemon=True, name=f"{self.name_prefix}-{channel}")
        self.channel = channel
        self.on_message = on_message
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        backoff = 2.0
        while not self.stopped:
            try:
                self._run_once()
                backoff = 2.0
            except Exception as e:  # noqa: BLE001 - keep chat alive no matter what
                log.warning("%s error: %s (reconnecting in %.0fs)", self.name, e, backoff)
            if self.stopped:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def _run_once(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


def make_chat_source(platform: str, channel: str, extra: dict, on_message: OnMessage) -> ChatSource | None:
    if platform == "twitch":
        from .twitch_irc import TwitchChat
        return TwitchChat(channel, on_message)
    if platform == "youtube":
        from .youtube_chat import YouTubeChat
        return YouTubeChat(channel, on_message, video_url=extra.get("url"))
    if platform == "kick":
        if not extra.get("chatroom_id"):
            log.warning("kick chatroom id unknown for %s; chat signal disabled", channel)
            return None
        from .kick_chat import KickChat
        return KickChat(channel, on_message, chatroom_id=extra["chatroom_id"])
    return None
