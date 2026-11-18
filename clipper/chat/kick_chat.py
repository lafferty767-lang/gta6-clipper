"""Kick chat via its public Pusher websocket (unofficial; key can be overridden by env)."""
from __future__ import annotations

import json
import os
import time

from . import ChatSource

PUSHER_KEY = os.environ.get("KICK_PUSHER_KEY", "32cbd69e4b950bf97679")
PUSHER_CLUSTER = os.environ.get("KICK_PUSHER_CLUSTER", "us2")


class KickChat(ChatSource):
    name_prefix = "kick-chat"

    def __init__(self, channel, on_message, chatroom_id):
        super().__init__(channel, on_message)
        self.chatroom_id = chatroom_id

    def _run_once(self) -> None:
        import websocket  # websocket-client

        url = (f"wss://ws-{PUSHER_CLUSTER}.pusher.com/app/{PUSHER_KEY}"
               "?protocol=7&client=js&version=8.4.0&flash=false")
        ws = websocket.create_connection(url, timeout=120)
        try:
            ws.send(json.dumps({"event": "pusher:subscribe",
                                "data": {"auth": "", "channel": f"chatrooms.{self.chatroom_id}.v2"}}))
            while not self.stopped:
                raw = ws.recv()
                if not raw:
                    raise ConnectionError("kick ws closed")
                evt = json.loads(raw)
                name = evt.get("event", "")
                if name == "pusher:ping":
                    ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                elif name.endswith("ChatMessageEvent"):
                    data = evt.get("data")
                    data = json.loads(data) if isinstance(data, str) else (data or {})
                    self.on_message(time.time(), data.get("content", ""))
        finally:
            ws.close()
