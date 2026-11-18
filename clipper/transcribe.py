"""Speech-to-text with word timestamps (faster-whisper), used for captions and the LLM judge."""
from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Whisper

log = logging.getLogger(__name__)
_model = None
_model_lock = threading.Lock()


@dataclass
class Word:
    start: float
    end: float
    text: str


def _get_model(cfg: Whisper):
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            log.info("loading whisper model %s (%s/%s)", cfg.model, cfg.device, cfg.compute_type)
            _model = WhisperModel(cfg.model, device=cfg.device, compute_type=cfg.compute_type)
        return _model


def extract_wav(src: Path, offset: float, duration: float, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{offset:.3f}",
                    "-i", str(src), "-t", f"{duration:.3f}", "-vn", "-ac", "1", "-ar", "16000", str(out)],
                   check=True)
    return out


def transcribe(wav: Path, cfg: Whisper, language: str | None = None) -> tuple[str, list[Word]]:
    try:
        model = _get_model(cfg)
    except Exception as e:  # noqa: BLE001 - captions are optional; never kill the clip
        log.warning("whisper unavailable (%s); continuing without captions", e)
        return "", []
    segments, _info = model.transcribe(str(wav), language=language, word_timestamps=True,
                                       vad_filter=True, beam_size=1)
    words: list[Word] = []
    texts = []
    for seg in segments:
        texts.append(seg.text.strip())
        for w in seg.words or []:
            if w.word.strip():
                words.append(Word(float(w.start), float(w.end), w.word.strip()))
    return " ".join(texts).strip(), words
