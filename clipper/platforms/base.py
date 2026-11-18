from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LiveInfo:
    platform: str          # twitch | youtube | kick
    channel: str           # login / handle / slug as configured
    is_live: bool
    url: str               # URL streamlink can open
    title: str = ""
    game: str = ""
    viewers: int = 0
    extra: dict[str, Any] = field(default_factory=dict)  # platform ids (broadcaster_id, chatroom_id, video_id...)

    @property
    def text_for_game_match(self) -> str:
        return f"{self.game} {self.title}"
