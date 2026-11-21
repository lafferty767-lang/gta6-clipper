"""Phone access through ntfy (https://ntfy.sh), free and with no account:

- address board: the bot posts its current public address to a private topic, and the permanent
  phone link (docs/go.html on GitHub Pages) reads it and opens the dashboard there. So the phone
  bookmark keeps working when the home tunnel's address changes.
- optional notifications: subscribe to the other topic in the ntfy app to hear about new clips."""
from __future__ import annotations

import logging
import os
import secrets
import threading

log = logging.getLogger(__name__)

NTFY_URL = "https://ntfy.sh"


def topic(db) -> str:
    """This bot's topic, made up on first use."""
    t = db.kv_get("ntfy_topic")
    if not t:
        t = "gta6clips-" + secrets.token_urlsafe(12).replace("-", "x").replace("_", "x").lower()
        db.kv_set("ntfy_topic", t)
    return t


def address_topic(db) -> str:
    t = db.kv_get("ntfy_address_topic")
    if not t:
        t = "gta6addr-" + secrets.token_urlsafe(12).replace("-", "x").replace("_", "x").lower()
        db.kv_set("ntfy_address_topic", t)
    return t


PAGES_GO = "https://lafferty767-lang.github.io/gta6-clipper/go.html"


def phone_link(db) -> str:
    return f"{PAGES_GO}#{address_topic(db)}"


def publish_address(db, address: str) -> bool:
    """Posts the dashboard's current address to the address board (no notification sound)."""
    import httpx
    try:
        r = httpx.post(NTFY_URL, json={"topic": address_topic(db), "message": address, "priority": 1}, timeout=15)
        ok = r.status_code == 200
    except httpx.HTTPError as e:
        log.warning("couldn't post the phone address: %s", e)
        ok = False
    db.kv_set("ntfy_address_last", f"{'ok' if ok else 'failed'} {address}")
    return ok


def enabled(db) -> bool:
    return db.kv_get("ntfy_off") != "1"


def public_link(path: str) -> str | None:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return base + path if base.startswith("https://") else None


def send(db, title: str, message: str, click: str | None = None, actions: list[tuple[str, str]] = (),
         wait: bool = False) -> bool:
    """actions: (button label, https url). Never raises; runs in the background unless wait=True."""
    if not enabled(db):
        return False
    body = {"topic": topic(db), "title": title[:200], "message": message[:1000] or title}
    if click:
        body["click"] = click
    acts = [{"action": "view", "label": label[:40], "url": url, "clear": False} for label, url in actions if url][:3]
    if acts:
        body["actions"] = acts
    result = {"ok": False}

    def post():
        import httpx
        try:
            r = httpx.post(NTFY_URL, json=body, timeout=15)
            result["ok"] = r.status_code == 200
            if not result["ok"]:
                log.warning("phone notification refused (%s): %s", r.status_code, r.text[:200])
        except httpx.HTTPError as e:
            log.warning("phone notification failed: %s", e)

    if wait:
        post()
    else:
        threading.Thread(target=post, daemon=True, name="notify").start()
    return result["ok"] if wait else True


def clip_ready(db, clip: dict) -> None:
    link = public_link(f"/clip/{clip['id']}")
    send(db, f"New {clip['streamer']} clip to review",
         clip.get("title") or "A new clip is waiting for you.",
         click=link, actions=[("Review", link)] if link else ())


def clip_posted(db, clip: dict, campaign_url: str | None) -> None:
    ups = clip.get("uploads") or {}
    yt = (ups.get("youtube") or {}).get("url")
    tiktok = "tiktok" in ups and (ups["tiktok"] or {}).get("ok")
    lines = [clip.get("title") or ""]
    if tiktok:
        lines.append("TikTok: it's in your TikTok inbox. Post it there, then submit the link.")
    if yt:
        lines.append(f"YouTube: {yt}")
    actions = [("Open YouTube Short", yt)] if yt else []
    if campaign_url:
        actions.append(("Submit to campaign", campaign_url))
    send(db, f"{clip['streamer']} clip posted", "\n".join(x for x in lines if x),
         click=campaign_url or yt, actions=actions)
