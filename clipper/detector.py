"""Finds clip-worthy moments from live signals.

Signals (all wall-clock timestamps):
  * chat velocity  - messages per window vs. the last few minutes (z-score)
  * hype           - messages containing hype emotes/keywords (z-score)
  * audio          - loudness spikes vs. the stream's own baseline (robust z-score)
  * viewer clips   - viewers pressing "Clip" on Twitch (strong, explicit signal)

Each tick we evaluate a window that ends slightly in the past (so the audio of the latest
completed segment is in), combine the signals into one score, and when it crosses the
threshold we "arm", follow the burst to its peak, then emit a Moment with clip bounds.
"""
from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass, field
from statistics import mean, median, pstdev

from .config import Detection

BASELINE_SECONDS = 300
Z_CAP = 10.0


@dataclass
class Moment:
    start: float          # clip start (wall clock)
    end: float            # clip end (wall clock)
    event_ts: float       # best guess of when the thing happened
    score: float
    signals: dict = field(default_factory=dict)
    chat_sample: list[str] = field(default_factory=list)


def _compile_hype(words: list[str]) -> re.Pattern:
    parts = []
    for w in words:
        if re.fullmatch(r"[\w ]+", w):
            parts.append(r"(?<!\w)" + re.escape(w) + r"(?!\w)")
        else:  # emoji / punctuation
            parts.append(re.escape(w))
    return re.compile("|".join(parts), re.IGNORECASE)


class MomentDetector:
    def __init__(self, cfg: Detection, started_at: float):
        self.cfg = cfg
        self.started_at = started_at
        self.chat: deque[tuple[float, bool]] = deque()      # (ts, is_hype)
        self.chat_text: deque[tuple[float, str]] = deque()  # last ~2 min of messages
        self.audio: deque[tuple[float, float]] = deque()    # (ts, dbfs)
        self.viewer_clips: deque[float] = deque()
        self.moments: deque[float] = deque()                # event ts of emitted moments
        self._hype_re = _compile_hype(cfg.hype_keywords)
        self._armed: dict | None = None
        self._last_end = 0.0
        self.last_score: dict = {}
        self._lock = threading.RLock()  # chat/audio arrive from other threads

    # --- inputs -------------------------------------------------------------------
    def add_chat(self, ts: float, text: str) -> None:
        is_hype = bool(self._hype_re.search(text or ""))
        with self._lock:
            self.chat.append((ts, is_hype))
            self.chat_text.append((ts, (text or "")[:200]))

    def add_audio(self, ts: float, dbfs: float) -> None:
        with self._lock:
            self.audio.append((ts, dbfs))

    def add_viewer_clip(self, ts: float) -> None:
        with self._lock:
            self.viewer_clips.append(ts)

    @property
    def eval_lag(self) -> float:
        return self.cfg.segment_seconds * 1.5 + 1

    # --- scoring ------------------------------------------------------------------
    def _trim(self, now: float) -> None:
        keep = now - BASELINE_SECONDS - 60
        for dq in (self.chat, self.audio):
            while dq and dq[0][0] < keep:
                dq.popleft()
        while self.chat_text and self.chat_text[0][0] < now - 120:
            self.chat_text.popleft()
        while self.viewer_clips and self.viewer_clips[0] < now - 600:
            self.viewer_clips.popleft()
        while self.moments and self.moments[0] < now - 3600:
            self.moments.popleft()

    def _count_z(self, t: float, values: list[float]) -> tuple[float, float]:
        """z-score of the event count in (t-W, t] against W-sized bins of the baseline period."""
        w = self.cfg.window_seconds
        cur = sum(1 for ts in values if t - w < ts <= t)
        base_start = max(self.started_at, t - w - BASELINE_SECONDS)
        nbins = int((t - w - base_start) // w)
        if nbins < 3:
            return 0.0, cur / w
        bins = [0] * nbins
        for ts in values:
            if base_start <= ts < base_start + nbins * w:
                bins[int((ts - base_start) // w)] += 1
        m = mean(bins)
        sd = max(pstdev(bins), m ** 0.5, 1.0)
        return min((cur - m) / sd, Z_CAP), cur / w

    def _audio_z(self, t: float) -> float:
        w = self.cfg.window_seconds
        cur = [db for ts, db in self.audio if t - w < ts <= t]
        base = [db for ts, db in self.audio if t - w - BASELINE_SECONDS <= ts <= t - w]
        if not cur or len(base) < 20:
            return 0.0
        med = median(base)
        mad = median(abs(x - med) for x in base) * 1.4826
        return min((max(cur) - med) / max(mad, 2.0), Z_CAP)

    def score_at(self, t: float) -> dict:
        chat_ts = [ts for ts, _ in self.chat]
        hype_ts = [ts for ts, h in self.chat if h]
        chat_z, rate = self._count_z(t, chat_ts)
        hype_z, _ = self._count_z(t, hype_ts)
        audio_z = self._audio_z(t)
        clips = sum(1 for ts in self.viewer_clips if t - 30 < ts <= t)
        wt = self.cfg.weights
        score = (wt.get("chat", 1) * max(chat_z, 0) + wt.get("hype", 1) * max(hype_z, 0)
                 + wt.get("audio", 0.8) * max(audio_z, 0) + wt.get("viewer_clip", 3) * clips)
        return {"t": t, "score": round(score, 2), "chat_z": round(chat_z, 2), "hype_z": round(hype_z, 2),
                "audio_z": round(audio_z, 2), "viewer_clips": clips, "chat_rate": round(rate, 2)}

    def tick(self, now: float) -> Moment | None:
        with self._lock:
            return self._tick(now)

    def _tick(self, now: float) -> Moment | None:
        self._trim(now)
        t = now - self.eval_lag
        s = self.score_at(t)
        self.last_score = s
        c = self.cfg
        if t - self.started_at < c.warmup_seconds:
            return None

        if self._armed is None:
            if (s["score"] >= c.threshold and t - self._last_end >= c.min_gap_seconds
                    and len(self.moments) < c.max_clips_per_stream_hour):
                self._armed = {"first": t, "peak": s, "last_above": t}
            return None

        a = self._armed
        if s["score"] > a["peak"]["score"]:
            a["peak"] = s
        if s["score"] >= c.threshold:
            a["last_above"] = t
        if s["score"] >= c.threshold * 0.5 and t - a["first"] < 15:
            return None  # still bursting: keep following it

        self._armed = None
        event = a["first"] - c.reaction_delay_seconds
        start = event - c.clip_before_seconds
        end = max(event + c.clip_after_seconds, a["last_above"] + 3)
        if end - start > c.max_clip_seconds:
            end = start + c.max_clip_seconds
        if end - start < c.min_clip_seconds:
            end = start + c.min_clip_seconds
        self._last_end = end
        self.moments.append(event)
        sample = [txt for ts, txt in self.chat_text if event - 5 <= ts <= end + 5][:40]
        return Moment(start=start, end=end, event_ts=event, score=a["peak"]["score"],
                      signals=a["peak"], chat_sample=sample)
