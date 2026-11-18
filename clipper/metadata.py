"""Scores a clip and writes its title/description/hashtags.

With ANTHROPIC_API_KEY set and llm.enabled, Claude judges the transcript + chat reaction and
writes the copy. Otherwise a simple template is used (and every clip passes).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .config import LLM, Streamer

log = logging.getLogger(__name__)

BASE_TAGS = ["#GTA6", "#GTAVI", "#shorts", "#gaming"]

SYSTEM = """You are the editor of a YouTube Shorts / TikTok channel that posts the funniest and most \
shocking moments from popular streamers playing GTA 6 live. You get an automatically detected \
moment: the streamer's speech transcript, a sample of live chat messages from that moment, and \
the signal scores that triggered it. Decide whether it would make a good standalone short and \
write the post copy.

Scoring guide (score 1-10):
- 8-10: clear payoff a stranger would get with no context (huge reaction, funny line, crazy in-game event).
- 5-7: decent reaction but needs context, or the payoff is weak.
- 1-4: nothing happens, chat spam about something off-screen (donations, ads, raids), loading screens, \
  or the transcript is just filler.

Titles: under 70 characters, curiosity-driven, written like a fan clip channel, may use one emoji, \
must name the streamer, no clickbait lies, no slurs. Never repeat offensive words from chat. \
Description: one or two short sentences. Hashtags: 3-6 relevant ones without the # symbol."""


class ClipJudgement(BaseModel):
    score: int = Field(description="1-10 how good this is as a standalone short")
    reason: str = Field(description="one sentence explaining the score")
    title: str
    description: str
    hashtags: list[str]


@dataclass
class ClipMeta:
    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)
    llm_score: float | None = None
    llm_reason: str = ""


def credit_line(streamer: Streamer, platform: str, channel: str) -> str:
    urls = {"twitch": f"twitch.tv/{channel}", "kick": f"kick.com/{channel}",
            "youtube": f"youtube.com/{channel if channel.startswith('@') else '@' + channel}"}
    return f"{streamer.name} live on {urls.get(platform, platform)}"


def _finalize(streamer: Streamer, platform: str, channel: str, title: str, desc: str, tags: list[str]) -> tuple[str, str, list[str]]:
    hashtags = []
    for t in [*tags, *streamer.hashtags, streamer.name.replace(" ", ""), *BASE_TAGS]:
        t = "#" + t.lstrip("#").replace(" ", "")
        if t.lower() not in {h.lower() for h in hashtags}:
            hashtags.append(t)
    hashtags = hashtags[:8]
    description = f"{desc}\n\n🎥 Credit: {credit_line(streamer, platform, channel)}\n\n{' '.join(hashtags)}"
    return title[:95], description, hashtags


def template_meta(streamer: Streamer, platform: str, channel: str, transcript: str) -> ClipMeta:
    hook = ""
    if transcript:
        first = transcript.split(".")[0].strip()
        hook = first if len(first) <= 45 else first[:45].rsplit(" ", 1)[0] + "..."
    title = f"{streamer.name} in GTA 6: \"{hook}\" 💀" if hook else f"{streamer.name} lost it in GTA 6 💀"
    t, d, h = _finalize(streamer, platform, channel, title, f"{streamer.name} playing GTA 6 live.", [])
    return ClipMeta(t, d, h)


def judge_clip(cfg: LLM, streamer: Streamer, platform: str, channel: str, stream_title: str,
               transcript: str, chat_sample: list[str], signals: dict) -> ClipMeta:
    if not (cfg.enabled and os.environ.get("ANTHROPIC_API_KEY")):
        return template_meta(streamer, platform, channel, transcript)
    import anthropic

    chat = "\n".join(f"- {m}" for m in chat_sample[:40]) or "(no chat captured)"
    user = (f"Streamer: {streamer.name} ({platform})\nStream title: {stream_title}\n"
            f"Signals: {signals}\n\nTranscript of the clip:\n{transcript or '(no speech detected)'}\n\n"
            f"Live chat during the moment:\n{chat}")
    client = anthropic.Anthropic()
    try:
        resp = client.messages.parse(
            model=cfg.model,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_format=ClipJudgement,
        )
    except anthropic.APIStatusError as e:
        log.warning("Claude request failed (%s); using template copy", e)
        return template_meta(streamer, platform, channel, transcript)
    except anthropic.APIConnectionError as e:
        log.warning("Claude unreachable (%s); using template copy", e)
        return template_meta(streamer, platform, channel, transcript)
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        log.info("Claude declined to judge this clip; marking low score")
        meta = template_meta(streamer, platform, channel, transcript)
        meta.llm_score, meta.llm_reason = 0, "model declined"
        return meta
    j: ClipJudgement = resp.parsed_output
    t, d, h = _finalize(streamer, platform, channel, j.title, j.description, j.hashtags)
    return ClipMeta(t, d, h, float(j.score), j.reason)
