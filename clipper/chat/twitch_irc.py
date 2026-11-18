"""Anonymous read-only Twitch chat over IRC (no account or token needed)."""
from __future__ import annotations

import random
import socket
import ssl
import time

from . import ChatSource


class TwitchChat(ChatSource):
    name_prefix = "twitch-chat"

    def _run_once(self) -> None:
        ctx = ssl.create_default_context()
        raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=30)
        with ctx.wrap_socket(raw, server_hostname="irc.chat.twitch.tv") as s:
            nick = f"justinfan{random.randint(10000, 99999)}"
            s.sendall(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{self.channel.lower()}\r\n".encode())
            s.settimeout(300)
            buf = b""
            while not self.stopped:
                chunk = s.recv(65536)
                if not chunk:
                    raise ConnectionError("twitch irc closed")
                buf += chunk
                *lines, buf = buf.split(b"\r\n")
                for line in lines:
                    self._handle(s, line.decode("utf-8", "replace"))

    def _handle(self, s, line: str) -> None:
        if line.startswith("PING"):
            s.sendall(line.replace("PING", "PONG", 1).encode() + b"\r\n")
            return
        # :nick!nick@nick.tmi.twitch.tv PRIVMSG #chan :message
        if " PRIVMSG " in line:
            _, _, msg = line.partition(" :")
            if " PRIVMSG " in msg:  # tags present -> message is after the 2nd " :"
                _, _, msg = msg.partition(" :")
            self.on_message(time.time(), msg)
