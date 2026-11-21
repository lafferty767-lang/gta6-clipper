"""Speech-to-text with word timestamps (faster-whisper), used for captions and the LLM judge."""
from __future__ import annotations

import logging
import subprocess
import threading
import wave
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
                    "-i", str(src), "-t", f"{duration:.3f}", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
                   check=True)
    return out


def transcribe(wav: Path, cfg: Whisper, language: str | None = None) -> tuple[str, list[Word]]:
    try:
        model = _get_model(cfg)
    except Exception as e:  # noqa: BLE001 - captions are optional; never kill the clip
        log.warning("whisper unavailable (%s); continuing without captions", e)
        return "", []
    words: list[Word] = []
    texts = []
    try:
        # Hand whisper the samples rather than the file name: its own file reader depends on the
        # PyAV version, and newer PyAV releases broke it ("unexpected keyword argument 'metadata_errors'").
        segments, _info = model.transcribe(load_wav(wav), language=language, word_timestamps=True,
                                           vad_filter=True, beam_size=1)
        for seg in segments:
            texts.append(seg.text.strip())
            for w in seg.words or []:
                if w.word.strip():
                    words.append(Word(float(w.start), float(w.end), w.word.strip()))
    except Exception as e:  # noqa: BLE001 - captions are optional; never kill the clip
        log.warning("transcription failed (%s); continuing without captions", e)
        return "", []
    return " ".join(texts).strip(), words


def load_wav(path: Path):
    """16 kHz mono 16-bit WAV (as written by extract_wav) -> float32 samples in [-1, 1]."""
    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
            raise ValueError(f"unexpected wav format {w.getnchannels()}ch {w.getframerate()}Hz {8 * w.getsampwidth()}bit")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
