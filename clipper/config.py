"""Loads config.yaml + environment variables into simple dataclasses."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_GAME_MATCH = ["grand theft auto vi", "grand theft auto 6", "gta 6", "gta vi", "gta6", "gtavi"]

DEFAULT_HYPE = [
    # emotes
    "KEKW", "OMEGALUL", "LUL", "LULW", "ICANT", "Pog", "PogU", "POGGERS", "monkaS", "monkaW",
    "Sadge", "PepeLaugh", "KEKL", "LMAO", "LMFAO", "W", "WWW", "L", "CLIP", "CLIP IT", "CLIPPED",
    "NO WAY", "BRO", "WTF", "HOLY", "INSANE", "💀", "😭", "😂", "🤣", "🔥", "‼️", "SPEED", "OHHH",
]


@dataclass
class Facecam:
    """Facecam box as fractions of the source frame (0..1)."""
    x: float
    y: float
    w: float
    h: float


@dataclass
class Streamer:
    name: str
    platforms: dict[str, str]  # platform -> handle / login / slug
    permission: str = "unknown"  # granted | unknown | denied
    permission_note: str = ""
    campaign_url: str = ""  # where to submit posted clips (paid clipping campaign)
    enabled: bool = True
    facecam: Facecam | None = None
    language: str | None = None  # whisper language hint, e.g. "en"
    hashtags: list[str] = field(default_factory=list)
    game_check: bool = True  # False = clip any stream from this person, not only GTA 6

    @property
    def upload_allowed(self) -> bool:
        return self.permission == "granted"


@dataclass
class Detection:
    segment_seconds: int = 4
    buffer_seconds: int = 300
    clip_before_seconds: int = 25
    clip_after_seconds: int = 15
    min_clip_seconds: int = 20
    max_clip_seconds: int = 59
    min_gap_seconds: int = 90
    warmup_seconds: int = 120
    window_seconds: int = 8
    reaction_delay_seconds: int = 4
    threshold: float = 6.0
    weights: dict[str, float] = field(default_factory=lambda: {
        "chat": 1.0, "hype": 1.0, "audio": 0.8, "viewer_clip": 3.0,
    })
    hype_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_HYPE))
    max_clips_per_stream_hour: int = 6


@dataclass
class LLM:
    enabled: bool = True
    model: str = "claude-opus-5-5"
    min_score: int = 6


@dataclass
class Whisper:
    model: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"


@dataclass
class Edit:
    layout: str = "auto"  # auto | split | blur
    width: int = 1080
    height: int = 1920
    facecam_height: int = 960  # split layout: height of the streamer half (of 1920)
    captions: bool = True
    credit_overlay: bool = True
    font_file: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font_name: str = "DejaVu Sans"
    crf: int = 19
    preset: str = "veryfast"
    loudnorm: bool = True


@dataclass
class Uploads:
    max_per_day: int = 5
    min_minutes_between: int = 20
    targets: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class Config:
    streamers: list[Streamer]
    review_mode: bool = True
    require_permission_to_upload: bool = True
    clip_without_permission: bool = False
    data_dir: Path = Path("./data")
    poll_interval_seconds: int = 60
    stream_quality: str = "1080p60,1080p,best"
    game_match: list[str] = field(default_factory=lambda: list(DEFAULT_GAME_MATCH))
    discovery: dict[str, Any] = field(default_factory=lambda: {"enabled": True, "interval_minutes": 15, "languages": ["en"], "top": 25})
    detection: Detection = field(default_factory=Detection)
    llm: LLM = field(default_factory=LLM)
    whisper: Whisper = field(default_factory=Whisper)
    edit: Edit = field(default_factory=Edit)
    uploads: Uploads = field(default_factory=Uploads)
    max_concurrent_streams: int = 4

    # --- secrets from environment -------------------------------------------------
    @staticmethod
    def env(name: str, default: str | None = None) -> str | None:
        v = os.environ.get(name, default)
        return v if v else default

    def is_gta6(self, text: str | None) -> bool:
        t = (text or "").lower()
        return any(m.lower() in t for m in self.game_match)


def _build(cls, data: dict | None):
    data = dict(data or {})
    known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
    return cls(**known)


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("CLIPPER_CONFIG", "config.yaml"))
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    streamers = []
    for s in raw.get("streamers", []):
        fc = s.get("facecam")
        streamers.append(Streamer(
            name=s["name"],
            platforms={k: str(v) for k, v in (s.get("platforms") or {}).items() if v},
            permission=str(s.get("permission", "unknown")).lower(),
            permission_note=s.get("permission_note", "") or "",
            campaign_url=s.get("campaign_url", "") or "",
            enabled=bool(s.get("enabled", True)),
            facecam=Facecam(**fc) if fc else None,
            language=s.get("language"),
            hashtags=list(s.get("hashtags") or []),
            game_check=bool(s.get("game_check", True)),
        ))

    det = _build(Detection, raw.get("detection"))
    if raw.get("detection", {}).get("weights"):
        det.weights = {**Detection().weights, **raw["detection"]["weights"]}

    cfg = Config(
        streamers=streamers,
        review_mode=bool(raw.get("review_mode", True)),
        require_permission_to_upload=bool(raw.get("require_permission_to_upload", True)),
        clip_without_permission=bool(raw.get("clip_without_permission", False)),
        data_dir=Path(os.environ.get("CLIPPER_DATA_DIR", raw.get("data_dir", "./data"))),
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 60)),
        stream_quality=raw.get("stream_quality", "1080p60,1080p,best"),
        game_match=[g.lower() for g in raw.get("game_match", DEFAULT_GAME_MATCH)],
        discovery={**Config.__dataclass_fields__["discovery"].default_factory(), **(raw.get("discovery") or {})},
        detection=det,
        llm=_build(LLM, raw.get("llm")),
        whisper=_build(Whisper, raw.get("whisper")),
        edit=_build(Edit, raw.get("edit")),
        uploads=_build(Uploads, raw.get("uploads")),
        max_concurrent_streams=int(raw.get("max_concurrent_streams", 4)),
    )
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
