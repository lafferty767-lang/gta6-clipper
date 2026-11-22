"""Keeps swearing and slurs out of post titles, descriptions and hashtags.

Streamers swear a lot, and the title often quotes them. Platforms push clean titles more, and the
account stays advertiser-friendly. Words are matched whole (so "class" or "hello" are fine), along
with common spellings like "fuuuck" or "sh*t".
"""
from __future__ import annotations

import re

_STEMS = [
    r"f+u+c+k+\w*", r"f+[\W_]*c+k+\w*", r"motherf\w*", r"sh+i+t+\w*", r"bull?sh\w*", r"b+i+t+c+h+\w*",
    r"a+s+s+(?:hole|holes|hat|wipe)?", r"dumbass\w*", r"jackass\w*", r"badass\w*", r"bastards?", r"dick(?:s|head\w*)?",
    r"cocks?", r"cocksucker\w*", r"cunts?", r"puss(?:y|ies)", r"whores?", r"slut\w*", r"piss\w*", r"crap\w*",
    r"damn\w*", r"goddamn\w*", r"dammit", r"hell", r"tits?", r"titties", r"boobs?", r"twats?", r"wank\w*", r"prick\w*",
    r"n+i+g+g+\w*", r"nigs?", r"fag\w*", r"retard\w*", r"spaz\w*", r"kys", r"wtf", r"stfu", r"lmf+ao+", r"af",
    r"porn\w*", r"nudes?", r"horny", r"sex\w*", r"cum\w*", r"jizz\w*", r"bollocks", r"bloody", r"thot\w*",
]
_BAD = re.compile(r"(?<![\w*])(?:" + "|".join(_STEMS) + r")(?![\w*])", re.IGNORECASE)
_STARRED = re.compile(r"\b\w+[*#@$%!]+\w*\b|\b\w*[*#@$%]+\w+\b")   # f*ck, sh!t, b@#$h


def is_clean(text: str) -> bool:
    return not (_BAD.search(text or "") or _STARRED.search(text or ""))


def safe_title(title: str, streamer_name: str) -> str:
    return title if is_clean(title) else f"{streamer_name} lost it in GTA 6 💀"


def mask_word(word: str) -> str:
    """One caption word with a swear or slur hidden: 'fucking!' -> '*******!'."""
    if is_clean(word):
        return word
    return re.sub(r"[\w*#@$%]", "*", word.rstrip(".,!?\"'")) + word[len(word.rstrip(".,!?\"'")):]


def clean_hook(transcript: str, max_len: int = 45) -> str:
    """The first clean, short-ish sentence of what the streamer said (for a quote title), or ''."""
    for part in re.split(r"(?<=[.!?])\s+|\.\s*", transcript or ""):
        s = part.strip().strip('"')
        if len(s.split()) < 2 or not is_clean(s):
            continue
        return s if len(s) <= max_len else s[:max_len].rsplit(" ", 1)[0] + "..."
    return ""


def scrub(text: str) -> str:
    """Drops lines with bad words (for descriptions)."""
    return "\n".join(line for line in (text or "").splitlines() if is_clean(line)).strip()
