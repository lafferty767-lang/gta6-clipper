"""Viral meter: a 0-100 estimate of how likely a clip is to do well, from what the bot measured.

It's a guide for picking which clips to post first, not a promise. What goes in:
- how big the moment was compared with this bot's other recent clips (chat, hype, audio score)
- whether viewers clipped it themselves on Twitch (the strongest sign people cared)
- how many signals spiked at once (chat AND audio AND hype is a real moment, not a loud noise)
- whether there's talking in it (captions and a spoken hook keep people watching)
- the AI judge's 1-10 score, when a Claude key is set
"""
from __future__ import annotations

from bisect import bisect_left


def score(clip: dict, recent_scores: list[float]) -> int:
    sig = clip.get("signals") or {}
    s = float(clip.get("score") or 0)
    pool = sorted(recent_scores) or [s]
    pct = bisect_left(pool, s) / max(len(pool) - 1, 1)              # 0..1, where this moment ranks
    pts = 40 * min(pct, 1.0)
    pts += {0: 0, 1: 12, 2: 18}.get(int(sig.get("viewer_clips") or 0), 20)
    spikes = sum([(sig.get("chat_z") or 0) >= 2, (sig.get("hype_z") or 0) >= 1.5, (sig.get("audio_z") or 0) >= 2.5])
    pts += (0, 7, 14, 20)[spikes]
    talk = len((clip.get("transcript") or "").strip())
    pts += 15 if talk >= 150 else 10 if talk >= 60 else 4 if talk else 0
    if pct >= 0.9:
        pts += 5
    meter = min(pts, 100)
    if clip.get("llm_score") is not None:
        meter = 0.5 * meter + 5 * float(clip["llm_score"])
    return int(round(meter))


def label(meter: int) -> tuple[str, str]:
    """(words, css class)"""
    if meter >= 85:
        return "Post it", "ok"
    if meter >= 70:
        return "Strong", "ok"
    if meter >= 50:
        return "Maybe", "warn"
    return "Skip", "bad"
