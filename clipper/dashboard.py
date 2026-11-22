"""Review dashboard: watch clips, edit titles, approve/reject, see who's live.

Protected by HTTP basic auth (DASHBOARD_USER / DASHBOARD_PASSWORD). The only routes without a login:
  /public/<token>.mp4       approved clips, for Metricool to fetch (random unguessable tokens)
  /about /privacy /terms    the pages Google's and TikTok's app forms ask links for
  /<name>.txt               the TikTok URL-verification file, once uploaded on the Setup page
  /healthz                  health check
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import secrets
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.concurrency import run_in_threadpool

from . import db as dbm
from . import clean, notify
from . import viral
from .config import load_config
from .connect import TikTokConnector, YouTubeDeviceConnector
from .doctor import FAIL, OFF, OK, WARN, run_checks
from .settings import GROUPS, masked

log = logging.getLogger(__name__)
security = HTTPBasic()


def _ago(ts: float | None) -> str:
    if not ts:
        return ""
    d = int(time.time() - ts)
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


_rev_file = Path(__file__).with_name("REVISION")
REVISION = ("version " + _rev_file.read_text().strip()) if _rev_file.exists() else ""


def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


CSS = """
:root{--bg:#0e0f13;--card:#181a21;--line:#2a2d38;--text:#e9eaf0;--muted:#9aa0b4;--accent:#ff4f8b;--ok:#3ddc97;--warn:#ffb020;--bad:#ff5d5d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:18px 20px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header h1{font-size:18px;margin:0}header .pill{font-size:12px;padding:3px 9px;border-radius:99px;background:var(--line);color:var(--muted)}
main{padding:20px;max-width:1300px;margin:0 auto}h2{font-size:15px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin:28px 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
video{width:100%;aspect-ratio:9/16;background:#000;border-radius:8px;max-height:520px}
input,textarea{width:100%;background:#0f1117;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit}
textarea{min-height:90px}label{font-size:12px;color:var(--muted);display:block;margin:8px 0 4px}
.row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}button{border:0;border-radius:8px;padding:9px 14px;font-weight:600;cursor:pointer}
.approve{background:var(--ok);color:#06240f}.reject{background:var(--line);color:var(--text)}.retry{background:var(--warn);color:#2a1a00}
.meter{margin:8px 0 2px}.bar{height:10px;border-radius:6px;background:var(--line);overflow:hidden;margin-top:4px}.bar span{display:block;height:100%}
.meta{font-size:13px;color:var(--muted)}.score{font-weight:700;color:var(--accent)}.warn{color:var(--warn)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
a{color:#8ab4ff}
nav{display:flex;gap:14px;margin-left:auto}nav a{color:var(--text);text-decoration:none;padding:6px 10px;border-radius:8px}nav a.on{background:var(--line)}
.checks td:first-child{width:28px}.st-ok{color:var(--ok)}.st-warn{color:var(--warn)}.st-fail{color:var(--bad)}.st-off{color:var(--muted)}
.code{font:700 28px/1.2 ui-monospace,monospace;letter-spacing:.12em;background:#0f1117;padding:10px 16px;border-radius:10px;display:inline-block;margin:6px 0}
.mono{font-family:ui-monospace,monospace;font-size:13px;word-break:break-all}textarea.cfg{min-height:70vh;font:13px/1.45 ui-monospace,monospace}
.ok{color:var(--ok)}.btn{display:inline-block;background:var(--accent);color:#fff;border-radius:8px;padding:9px 14px;font-weight:600;text-decoration:none}
.btn.go{background:var(--ok);color:#06240f}.btn.plain{background:var(--line);color:var(--text)}button.plain{background:var(--line);color:var(--text)}
.tbl{overflow-x:auto}.title{font-weight:600;margin:2px 0 4px}.list{display:grid;gap:10px}
details.fold{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:12px 0}
details.fold>summary{padding:13px 14px;cursor:pointer;font-weight:600;list-style:none}
details.fold>summary::-webkit-details-marker{display:none}details.fold>summary:after{content:"\\25B8";float:right;color:var(--muted)}
details.fold[open]>summary:after{content:"\\25BE"}details.fold>.in{padding:0 14px 14px}
details.more summary{font-size:13px;color:var(--muted);cursor:pointer;margin-top:6px}
.count{color:var(--muted);font-weight:400}.done{color:var(--ok);font-size:13px}
@media(max-width:640px){
header{padding:10px 12px;gap:8px}header h1{font-size:16px}header>.meta{display:none}
nav{margin-left:0;gap:6px;width:100%}nav a{flex:1;text-align:center;background:var(--card)}
main{padding:12px}.grid{grid-template-columns:1fr;gap:12px}video{max-height:62vh}
.row button,.row .btn{flex:1;text-align:center;padding:12px 14px;font-size:16px}
input,textarea{font-size:16px}h2{margin:20px 0 10px}td,th{padding:6px;font-size:13px}
}
"""

JS = """
function copyText(t){var done=false;try{if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t);done=true}}catch(e){}
if(!done){var a=document.createElement('textarea');a.value=t;a.setAttribute('readonly','');a.style.position='fixed';a.style.opacity='0';
document.body.appendChild(a);a.select();try{document.execCommand('copy')}catch(e){}a.remove()}}
function submitClip(el){copyText(el.dataset.copy);fetch('/clips/'+el.dataset.id+'/submitted',{method:'POST'});
el.textContent='Link copied. Paste it on the campaign page';return true}
function copyBtn(el){copyText(el.dataset.copy);el.textContent='Copied';return false}
"""


VERIFY_FILE_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}\.txt$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(:[0-9]+)?$")

PUBLIC_PAGES = {
    "about": ("About", """<p>GTA 6 Clipper is a personal, self-hosted tool. It watches public live streams of Grand Theft Auto VI,
finds the funniest and most exciting moments, cuts them into short vertical clips, and posts them to the YouTube and TikTok
accounts of the person who runs this server. Only clips of streamers who allowed it are posted.</p>
<p>Nobody else signs in to this app; it acts only on its owner's own accounts.</p>
<p><a href="/privacy">Privacy policy</a> · <a href="/terms">Terms of service</a></p>"""),
    "privacy": ("Privacy policy", """<p>This app is used only by the owner of this server, on their own accounts.</p>
<p><b>What it accesses.</b> When the owner connects YouTube or TikTok, the app receives sign-in tokens for that one account.
It uses them only to upload clips the owner made with this app and to show which channel is connected. It does not read
your videos, comments, subscribers or any other account data.</p>
<p><b>Where it's kept.</b> Tokens and clips are stored only on this server. They are never sold, shared or sent anywhere
except to the platform they belong to (Google/YouTube or TikTok) to upload the owner's clips.</p>
<p><b>Live stream data.</b> Public chat messages from the streams being watched are counted to detect exciting moments and are
not stored.</p>
<p><b>Google data.</b> Use of information received from Google APIs adheres to the
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data Policy</a>,
including the Limited Use requirements. YouTube features are provided by the YouTube API Services; see the
<a href="https://policies.google.com/privacy">Google Privacy Policy</a>.</p>
<p><b>Removing access.</b> Disconnect the app any time at <a href="https://myaccount.google.com/permissions">Google account
permissions</a> or in TikTok under Settings and privacy, Security, Apps. Deleting this server deletes everything it stored.</p>"""),
    "terms": ("Terms of service", """<p>This app is a personal tool for its owner. It is provided as-is, with no warranty.</p>
<p>By using it you agree to the <a href="https://www.youtube.com/t/terms">YouTube Terms of Service</a> and the
<a href="https://www.tiktok.com/legal/terms-of-service">TikTok Terms of Service</a>, and to post clips only of creators
who have given permission.</p>"""),
}


def create_app(orch) -> FastAPI:
    app = FastAPI(title="GTA 6 Clipper", docs_url=None, redoc_url=None, openapi_url=None)
    user = os.environ.get("DASHBOARD_USER", "admin")
    password = os.environ.get("DASHBOARD_PASSWORD")
    if not password:
        password = secrets.token_urlsafe(12)
        log.warning("DASHBOARD_PASSWORD not set; generated one for this run: %s", password)

    def auth(creds: HTTPBasicCredentials = Depends(security)):
        ok = secrets.compare_digest(creds.username, user) and secrets.compare_digest(creds.password, password)
        if not ok:
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    def same_origin(request: Request):
        """Blocks cross-site form posts (the browser would attach the saved login to them)."""
        origin = request.headers.get("origin") or request.headers.get("referer")
        ok_hosts = {request.headers.get("host"), urlparse(orch.public_base_url or "").netloc}
        if origin and urlparse(origin).netloc not in ok_hosts:
            raise HTTPException(403, "cross-site request blocked")

    db: dbm.DB = orch.db
    write = [Depends(auth), Depends(same_origin)]

    def signed_in(request: Request) -> bool:
        import base64
        h = request.headers.get("authorization") or ""
        if not h.lower().startswith("basic "):
            return False
        try:
            u, _, pw = base64.b64decode(h[6:]).decode().partition(":")
        except ValueError:
            return False
        return secrets.compare_digest(u, user) and secrets.compare_digest(pw, password)

    @app.exception_handler(Exception)
    async def crashed(request: Request, exc: Exception):
        """Shows what broke instead of a bare "Internal Server Error", so a screenshot is enough to fix it."""
        log.exception("dashboard page %s crashed", request.url.path)
        if not signed_in(request):
            return PlainTextResponse("Internal Server Error", 500)
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-6:])
        return HTMLResponse(
            f"<!doctype html><meta charset=utf-8><body style='font:15px system-ui;padding:20px'>"
            f"<h2>This page hit an error</h2><p>Send a screenshot of this to get it fixed. "
            f"<a href='/setup'>Setup</a> · <a href='/settings'>Settings</a></p>"
            f"<pre style='white-space:pre-wrap;background:#eee;padding:12px'>{e(tb)}</pre>", 500)

    def reload_after_keys() -> None:
        orch.reload_integrations()
        with checks_lock:
            checks_cache.clear()
            checks_cache["gen"] = checks_gen[0] = checks_gen[0] + 1

    def on_youtube_token(token: str) -> None:
        orch.settings.update({"YOUTUBE_REFRESH_TOKEN": token})
        reload_after_keys()

    def on_tiktok_tokens(j: dict) -> None:
        orch.settings.update({"TIKTOK_REFRESH_TOKEN": j["refresh_token"]})
        db.kv_set("tiktok_token", json.dumps({
            "access_token": j.get("access_token", ""), "refresh_token": j["refresh_token"],
            "expires_at": time.time() + int(j.get("expires_in", 0) or 0)}))
        reload_after_keys()

    yt_connector = YouTubeDeviceConnector(on_youtube_token)
    tt_connector = TikTokConnector(on_tiktok_tokens)
    checks_cache: dict = {}
    checks_gen = [0]          # bumped when keys change, so a check run that started before is thrown away
    checks_lock = threading.Lock()

    def go(path: str, **params) -> RedirectResponse:
        return RedirectResponse(f"{path}?{urlencode(params)}" if params else path, 303)

    def page(active: str, body: str, refresh: int | None = None) -> HTMLResponse:
        links = [("/", "Clips"), ("/setup", "Setup"), ("/settings", "Settings")]
        nav = "".join(f"<a href='{h}' class={'on' if h == active else ''}>{t}</a>" for h, t in links)
        meta = f"<meta http-equiv=refresh content='{refresh};url={active}'>" if refresh else ""
        return HTMLResponse(
            "<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>GTA 6 Clipper</title><style>{CSS}</style><script>{JS}</script>{meta}</head><body>"
            f"<header><h1>GTA 6 Clipper</h1><nav>{nav}</nav><span class=meta>{e(REVISION)}</span></header><main>{body}</main></body></html>")

    def recent_scores() -> list[float]:
        return [float(x["score"] or 0) for x in db.list_clips(None, 300) if x.get("final_path")]

    def meter_html(c: dict) -> str:
        if "_meter" not in c or not c.get("final_path"):
            return ""
        m = c["_meter"]
        words, cls = viral.label(m)
        color = {"ok": "var(--ok)", "warn": "var(--warn)", "bad": "var(--bad)"}[cls]
        return (f"<div class=meter title='Viral meter: an estimate from chat, hype, audio, viewer clips and talking'>"
                f"<div class=row style='justify-content:space-between'><b>Viral meter</b><b class={cls}>{m}% · {words}</b></div>"
                f"<div class=bar><span style='width:{m}%;background:{color}'></span></div></div>")

    def with_meters(rows: list[dict], sort: bool = False) -> list[dict]:
        pool = recent_scores()
        for c in rows:
            c["_meter"] = viral.score(c, pool)
        return sorted(rows, key=lambda c: -c["_meter"]) if sort else rows

    def tiktok_caption(c: dict) -> str:
        """TikTok inbox drafts arrive without a caption, so the dashboard offers one to copy."""
        tags = [h for h in (c.get("hashtags") or []) if clean.is_clean(str(h).lstrip("#")) and h.lower() != "#shorts"]
        if "#fyp" not in [h.lower() for h in tags]:
            tags.append("#fyp")
        return f"{clean.safe_title(c.get('title') or '', c['streamer'])} {' '.join(tags)}".strip()

    def clip_card(c: dict, actions: str) -> str:
        sig = c.get("signals") or {}
        ups = c.get("uploads") or {}
        up_txt = " · ".join(
            f"{k}: " + (f"<a href='{e(v.get('url'))}' target=_blank>ok</a>" if v.get("ok") and v.get("url")
                        else ("ok" if v.get("ok") else f"<span class=bad>{e(str(v.get('error') or '')[:80])}</span>"))
            for k, v in ups.items() if isinstance(v, dict))
        video = (f'<video controls preload=metadata src="/media/{c["id"]}"></video>' if c.get("final_path")
                 else "<div class='meta warn'>No video: this clip broke before it was finished (reason below).</div>")
        cap = ""
        if c.get("status") in (dbm.APPROVED, dbm.UPLOADING, dbm.UPLOADED) and has_tiktok():
            cap = (f"<label>TikTok caption (paste it when you post the draft)</label>"
                   f"<textarea readonly rows=3 onclick='this.select()'>{e(tiktok_caption(c))}</textarea>"
                   f"<div class=row><button type=button class=plain data-copy=\"{e(tiktok_caption(c))}\" "
                   f"onclick='return copyBtn(this)'>Copy TikTok caption</button></div>")
        ai = f" · AI <span class=score>{e(c['llm_score'])}/10</span>" if c.get("llm_score") is not None else ""
        return f"""<div class=card>
{video}
{meter_html(c)}
<div class=title>{e(c.get('title') or '')}</div>
<div class=meta><b>{e(c['streamer'])}</b> · {_ago(c.get('moment_at'))}{ai}</div>
{f"<div class=meta>{e(c.get('llm_reason'))}</div>" if c.get('llm_reason') else ''}
{f"<div class=meta>{up_txt}</div>" if up_txt else ''}
{f"<div class='meta bad'>{e(c.get('error'))}</div>" if c.get('error') else ''}
<details class=more><summary>Details</summary><div class=meta>#{c['id']} on {e(c['platform'])} · signal {e(c.get('score'))} ·
 chat z {e(sig.get('chat_z'))} · hype z {e(sig.get('hype_z'))} · audio z {e(sig.get('audio_z'))} · viewer clips {e(sig.get('viewer_clips'))}</div></details>
{cap}{actions}</div>"""

    def has_tiktok() -> bool:
        return any(u.name == "tiktok" for u in (getattr(orch, "uploaders", None) or []))

    def youtube_url(c: dict) -> str | None:
        v = (c.get("uploads") or {}).get("youtube") or {}
        return v.get("url") if v.get("ok") else None

    def submitted(c: dict) -> bool:
        return bool(db.kv_get(f"campaign_submitted_{c['id']}"))

    def submit_html(c: dict) -> str:
        """One tap: copies the post's link and opens that streamer's campaign page to paste it into."""
        s = orch.streamer_by_name(c["streamer"])
        url = youtube_url(c)
        if not url:
            return ""
        note = "<div class=done>Submitted ✓ (tap again if you need to)</div>" if submitted(c) else ""
        if s and s.campaign_url:
            return (f"<div class=row><a class='btn go' href='{e(s.campaign_url)}' target=_blank rel=noopener "
                    f"data-copy='{e(url)}' data-id={c['id']} onclick='return submitClip(this)'>"
                    f"Copy link &amp; open {e(c['streamer'])} campaign</a></div>{note}")
        return (f"<div class=row><button type=button class=plain data-copy='{e(url)}' onclick='return copyBtn(this)'>"
                f"Copy YouTube link</button></div>")

    def posted_card(c: dict) -> str:
        """A posted clip without the video: links, the campaign button and the TikTok caption."""
        cap = (f"<div class=row><button type=button class=plain data-copy=\"{e(tiktok_caption(c))}\" "
               f"onclick='return copyBtn(this)'>Copy TikTok caption</button></div>") if has_tiktok() else ""
        yt = youtube_url(c)
        return f"""<div class=card><div class=title>{e(c.get('title') or '')}</div>
<div class=meta><b>{e(c['streamer'])}</b> · posted {_ago(max([v.get('at') or 0 for v in (c.get('uploads') or {}).values() if isinstance(v, dict)] or [0]) or None)}
{f" · <a href='{e(yt)}' target=_blank>YouTube</a>" if yt else ''} · <a href='/clip/{c['id']}'>open</a></div>
{submit_html(c)}{cap}</div>"""

    def queued_card(c: dict) -> str:
        ai = f" · AI {e(c['llm_score'])}/10" if c.get("llm_score") is not None else ""
        label = "uploading now" if c["status"] == dbm.UPLOADING else "waiting its turn"
        stop = (f"<form method=post action='/clips/{c['id']}/reject'><div class=row>"
                f"<button class=plain>Don't post this one</button></div></form>") if c["status"] == dbm.APPROVED else ""
        return f"""<div class=card><div class=title>{e(c.get('title') or '')}</div>
<div class=meta><b>{e(c['streamer'])}</b> · {label}{ai} · <a href='/clip/{c['id']}'>watch</a></div>{stop}</div>"""

    def fold(title: str, count: int | None, inner: str, open_: bool = False) -> str:
        n = f" <span class=count>({count})</span>" if count is not None else ""
        return f"<details class=fold{' open' if open_ else ''}><summary>{title}{n}</summary><div class=in>{inner}</div></details>"

    def review_form(c: dict, note: str = "", back: str = "/") -> str:
        return f"""<form method=post action="/clips/{c['id']}/approve">
<input type=hidden name=back value="{e(back)}"><label>Title</label><input name=title value="{e(clean.safe_title(c.get('title') or '', c['streamer']))}" maxlength=100>
<details class=more><summary>Edit description</summary><textarea name=description>{e(clean.scrub(c.get('description') or ''))}</textarea></details>
{note}<div class=row><button class=approve>Approve</button>
<button class=reject formaction="/clips/{c['id']}/reject">Reject</button></div></form>"""

    def safe_back(back: str) -> str:
        return back if re.fullmatch(r"/(clip/\d+)?", back or "") else "/"

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def index(more: int = 0):
        st = orch.status()
        cfg = orch.cfg
        live_rows = "".join(
            f"<tr><td><b>{e(w['streamer'])}</b></td><td>{e(w['platform'])}</td><td><a href='{e(w['url'])}' target=_blank>{e((w.get('title') or '')[:80])}</a></td>"
            f"<td>{e(w['viewers'])}</td><td>{_ago(w['since'])}</td><td>{e(w['clips'])}</td><td>{e((w.get('score') or {}).get('score'))}</td></tr>"
            for w in st["workers"]) or "<tr><td colspan=7 class=meta>Nobody on the list is live on GTA 6 right now.</td></tr>"
        check_rows = "".join(
            f"<tr><td>{e(s.name)}</td><td>{'on' if s.enabled else 'off'}</td>"
            f"<td class={'ok' if s.permission == 'granted' else 'warn'}>{e(s.permission)}</td>"
            f"<td>{e(', '.join(f'{p}: ' + ('LIVE' if v['live'] else 'offline') + (' (GTA 6)' if v['live'] and v['gta6'] else '') for p, v in (st['checks'].get(s.name, {}).get('platforms') or {}).items()))}</td></tr>"
            for s in cfg.streamers)
        all_ready = with_meters(db.list_clips([dbm.READY], 500), sort=True)   # best first
        shown = 30 if more else 6
        ready = all_ready[:shown]
        needs = db.list_clips([dbm.NEEDS_PERMISSION], 30)
        low = db.list_clips([dbm.LOW_SCORE], 12)
        failed = db.list_clips([dbm.FAILED], 12)
        queued = db.list_clips([dbm.UPLOADING, dbm.APPROVED], 20)
        posted = db.list_clips([dbm.UPLOADED], 30)
        week = time.time() - 7 * 86400
        to_submit = [c for c in posted if youtube_url(c) and not submitted(c) and (c.get("created_at") or 0) > week
                     and (lambda s: s and s.campaign_url)(orch.streamer_by_name(c["streamer"]))]
        disc = db.list_discovered(6)
        listed = {h.lower() for s in cfg.streamers for h in s.platforms.values()}
        disc_rows = "".join(
            f"<tr><td><a href='https://twitch.tv/{e(d['login'])}' target=_blank>{e(d['display_name'])}</a>{' ✓ on your list' if d['login'].lower() in listed else ''}</td>"
            f"<td>{e(d['viewers'])}</td><td>{e(d['language'])}</td><td>{e((d['title'] or '')[:90])}</td><td>{_ago(d['last_seen'])}</td></tr>"
            for d in disc) or "<tr><td colspan=5 class=meta>Needs Twitch API keys.</td></tr>"

        def cards(rows, fn, empty="None."):
            return "<div class=grid>" + "".join(fn(c) for c in rows) + "</div>" if rows else f"<p class=meta>{empty}</p>"

        retry = lambda c: clip_card(c, f"<form method=post action='/clips/{c['id']}/retry'><div class=row><button class=retry>Retry upload</button></div></form>" if c.get("final_path") else "")  # noqa: E731
        perm_note = lambda c: review_form(c, f"<p class='meta warn'>{e(c['streamer'])} has not granted permission. Once they have, set <code>permission: granted</code> on the <a href='/settings'>Settings</a> page; until then approving is blocked.</p>")  # noqa: E731
        live_names = ", ".join(w["streamer"] for w in st["workers"]) or "nobody on your list"
        more_link = (f"<p><a class='btn plain' href='/?more=1'>Show more ({len(all_ready) - shown} more)</a></p>"
                     if len(all_ready) > shown else "")
        submit_part = (f"<h2>Submit to campaigns ({len(to_submit)})</h2><div class=list>{''.join(posted_card(c) for c in to_submit)}</div>"
                       if to_submit else "")
        body = f"""<div class=meta>Live now: {e(live_names)} · uploads to {e(', '.join(st['uploaders']) or 'nothing yet, see Setup')} · max {cfg.uploads.max_per_day}/day{'' if cfg.review_mode else ' · auto-upload ON'}</div>
{submit_part}
<h2>Review: best first ({len(all_ready)})</h2>{cards(ready, lambda c: clip_card(c, review_form(c)), "Nothing waiting. New clips show up here.")}{more_link}
<h2>Up next to post ({len(queued)})</h2>{f"<div class=list>{''.join(queued_card(c) for c in queued)}</div>" if queued else "<p class=meta>Nothing approved right now.</p>"}
{fold("Posted", len(posted), f"<div class=list>{''.join(posted_card(c) for c in posted)}</div>" if posted else "<p class=meta>None yet.</p>")}
{fold("Failed", len(failed), cards(failed, retry)) if failed else ''}
{fold("Blocked: no permission from streamer", len(needs), cards(needs, lambda c: clip_card(c, perm_note(c)))) if needs else ''}
{fold("AI said skip", len(low), cards(low, lambda c: clip_card(c, review_form(c)))) if low else ''}
{fold("Live now", len(st['workers']), f"<div class=tbl><table><tr><th>Streamer</th><th>Platform</th><th>Title</th><th>Viewers</th><th>Since</th><th>Clips</th><th>Score now</th></tr>{live_rows}</table></div>")}
{fold("Your streamer list", len(cfg.streamers), f"<div class=tbl><table><tr><th>Name</th><th>Enabled</th><th>Permission</th><th>Last check</th></tr>{check_rows}</table></div>")}
{fold("Other big GTA 6 streams on Twitch", None, f"<div class=tbl><table><tr><th>Channel</th><th>Viewers</th><th>Lang</th><th>Title</th><th>Seen</th></tr>{disc_rows}</table></div>")}
"""
        return page("/", body)

    @app.post("/clips/{clip_id}/submitted", dependencies=write)
    def mark_submitted(clip_id: int):
        db.kv_set(f"campaign_submitted_{clip_id}", str(time.time()))
        return {"ok": True}

    @app.get("/media/{clip_id}", dependencies=[Depends(auth)])
    def media(clip_id: int):
        c = db.get_clip(clip_id)
        if not c or not c.get("final_path") or not Path(c["final_path"]).exists():
            raise HTTPException(404)
        return FileResponse(c["final_path"], media_type="video/mp4")

    @app.get("/public/{token}.mp4")
    def public_media(token: str):
        c = db.get_clip_by_token(token)
        if not c or c["status"] not in (dbm.APPROVED, dbm.UPLOADING, dbm.UPLOADED, dbm.FAILED) \
                or not c.get("final_path") or not Path(c["final_path"]).exists():
            raise HTTPException(404)
        return FileResponse(c["final_path"], media_type="video/mp4")

    @app.post("/clips/{clip_id}/approve", dependencies=write)
    def approve(clip_id: int, title: str = Form(...), description: str = Form(""), back: str = Form("/")):
        c = db.get_clip(clip_id)
        if not c:
            raise HTTPException(404)
        s = orch.streamer_by_name(c["streamer"])
        if orch.cfg.require_permission_to_upload and not (s and s.upload_allowed):
            db.update_clip(clip_id, title=title, description=description, status=dbm.NEEDS_PERMISSION)
            return HTMLResponse(f"<p>{e(c['streamer'])} has not granted permission (see Settings). Not uploading. <a href='/'>Back</a></p>", 403)
        db.update_clip(clip_id, title=title.strip(), description=description, status=dbm.APPROVED)
        return RedirectResponse(safe_back(back), 303)

    @app.post("/clips/{clip_id}/reject", dependencies=write)
    def reject(clip_id: int, back: str = Form("/")):
        db.update_clip(clip_id, status=dbm.REJECTED)
        return RedirectResponse(safe_back(back), 303)

    @app.get("/clip/{clip_id}", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def one_clip(clip_id: int):
        """The page a phone notification opens: one clip, with Approve and Reject."""
        c = db.get_clip(clip_id)
        if not c:
            raise HTTPException(404)
        back = f"/clip/{clip_id}"
        if c["status"] in (dbm.READY, dbm.LOW_SCORE):
            actions = review_form(c, back=back)
        else:
            label = {dbm.APPROVED: "Approved: it uploads shortly.", dbm.UPLOADING: "Uploading now.",
                     dbm.UPLOADED: "Posted.", dbm.REJECTED: "Rejected.", dbm.NEEDS_PERMISSION:
                     "Blocked: this streamer isn't set to permission: granted."}.get(c["status"], c["status"])
            actions = f"<p class='meta ok'>{e(label)}</p>" + (submit_html(c) if c["status"] == dbm.UPLOADED else "")
        c = with_meters([c])[0]
        waiting = with_meters([x for x in db.list_clips([dbm.READY], 200) if x["id"] != clip_id], sort=True)
        nxt = (f"<p><a class=btn href='/clip/{waiting[0]['id']}'>Next best clip ({waiting[0]['_meter']}%, {len(waiting)} left)</a></p>"
               if waiting else "<p class=meta>Nothing else is waiting for review.</p>")
        return page("/", f"<div class=grid>{clip_card(c, actions)}</div>{nxt}")

    @app.post("/clips/{clip_id}/retry", dependencies=write)
    def retry(clip_id: int):
        db.update_clip(clip_id, status=dbm.APPROVED, error=None)
        return RedirectResponse("/", 303)

    # --- Setup: keys, account connections, health checks ------------------------
    ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌", OFF: "➖"}

    def get_checks(force: bool = False):
        # The checks call outside services (up to ~20 s each), so they run without holding the lock.
        with checks_lock:
            if not force and "items" in checks_cache and time.time() - checks_cache["at"] <= 300:
                return checks_cache["items"], checks_cache["at"]
            gen = checks_gen[0]
        items = run_checks(orch.cfg, db, orch.public_base_url)
        at = time.time()
        with checks_lock:
            if gen == checks_gen[0]:
                checks_cache.update(items=items, at=at)
        return items, at

    def base_url(request: Request) -> str:
        """The public https address, or the address this page was opened on."""
        return (orch.public_base_url or f"https://{request.headers.get('host', 'localhost')}").rstrip("/")

    @app.get("/setup", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def setup(request: Request, refresh: int = 0, msg: str = ""):
        checks, at = get_checks(force=bool(refresh))
        rows = "".join(
            f"<tr><td>{ICON.get(c.status, '')}</td><td><b>{e(c.name)}</b></td><td class=st-{c.status}>{e(c.detail)}"
            f"{'<div class=meta>' + e(c.fix) + '</div>' if c.fix and c.status != OK else ''}</td></tr>" for c in checks)
        flow = yt_connector.flow
        if flow.status == "waiting":
            yt_box = (f"<p>On your phone or computer open <a href='{e(flow.verification_url)}' target=_blank>{e(flow.verification_url)}</a>, "
                      f"sign in with the Google account that owns your YouTube channel, and enter:</p><div class=code>{e(flow.user_code)}</div>"
                      "<p class=meta>This page refreshes by itself until it's done.</p>")
        else:
            note = f"<p class={'ok' if flow.status == 'done' else 'bad'}>{e(flow.message)}</p>" if flow.message else ""
            yt_box = f"{note}<form method=post action='/connect/youtube'><button class=approve>Connect YouTube</button></form>"
        tt_redirect = TikTokConnector.redirect_uri()
        base = base_url(request)
        vf = json.loads(db.kv_get("tiktok_verify") or "{}")
        vf_note = (f"<p class='meta ok'>Serving <a href='/{e(vf['name'])}' target=_blank>/{e(vf['name'])}</a>. "
                   "Press Verify in TikTok.</p>" if vf.get("name") else "")
        tt_box = ("<p class=meta>In your TikTok developer app, add this exact Redirect URI under Login Kit:</p>"
                  f"<div class='card mono'>{e(tt_redirect or 'not available: the server has no public HTTPS address yet')}</div>"
                  f"<p class=meta>TikTok also asks you to verify a URL prefix. Enter <span class=mono>{e(base)}/</span>, "
                  "download the file TikTok gives you, and upload it here:</p>"
                  "<form method=post action='/setup/tiktok-verify' enctype=multipart/form-data>"
                  "<input type=file name=file accept='.txt'><div class=row><button class=reject>Upload file</button></div></form>"
                  f"{vf_note}<div class=row><a class=btn href='/connect/tiktok'>Connect TikTok</a></div>")
        forms = []
        for gid, title, fields in GROUPS:
            inputs = "".join(
                f"<label>{e(f.label)} <span class=meta>{'· saved: ' + e(masked(orch.settings.get(f.key))) if orch.settings.get(f.key) else '· not set'}</span></label>"
                f"<input name={f.key} type={'password' if f.secret else 'text'} autocomplete=off placeholder='{e(f.help)}'>"
                for f in fields)
            extra = yt_box if gid == "youtube" else tt_box if gid == "tiktok" else ""
            forms.append(f"<div class=card><h3>{e(title)}</h3><form method=post action='/setup/keys'>{inputs}"
                         f"<div class=row><button class=approve>Save</button></div></form>{extra}</div>")
        banner = f"<div class=card><b>{e(msg)}</b></div>" if msg else ""
        here = request.headers.get("host", "")
        switch = ""
        if (request.headers.get("x-forwarded-proto") == "https" and HOST_RE.match(here)
                and urlparse(orch.public_base_url or "").netloc != here):
            switch = ("<form method=post action='/setup/address'><div class=row>"
                      f"<button class=reject>Use https://{e(here)} as the main address</button></div></form>"
                      "<p class=meta>Do this if the main address shows a certificate error but this one works. "
                      "The TikTok Redirect URI below changes with it.</p>")
        links = (f"<div class=card><h3>Your server's public pages</h3><p class=meta>Main address: "
                 f"<span class=mono>{e(orch.public_base_url or 'not set')}</span></p>{switch}"
                 "<p class=meta>Google's and TikTok's app forms ask for these links:</p><table>"
                 + "".join(f"<tr><td>{t}</td><td class=mono><a href='/{k}' target=_blank>{e(base)}/{k}</a></td></tr>"
                           for k, (t, _) in PUBLIC_PAGES.items())
                 + "</table></div>")
        phone_url = notify.phone_link(db)
        try:
            import segno
            qr = segno.make(phone_url, error="m").svg_inline(scale=4, dark="#000", light="#fff", border=3)
        except Exception:  # noqa: BLE001
            qr = ""
        last = (db.kv_get("ntfy_address_last") or "").split(" ", 1)
        board = ("<p class='meta ok'>Your Mac has posted its address for the phone link.</p>" if last[0] == "ok"
                 else "<p class='meta warn'>Your Mac couldn't post its address yet (needs internet). The link works once it does.</p>"
                 if last[0] == "failed" else "<p class=meta>Your Mac posts its address within a minute of starting.</p>")
        phone = ("<div class=card><h3>Use it on your phone</h3>"
                 "<p class=meta>Point your phone's camera at this code, or text yourself the link below. It opens this "
                 "dashboard from anywhere, and keeps working when your Mac's internet address changes. Log in with "
                 "your dashboard username and password. The first time, the link shows an Open dashboard button: before tapping it, "
                 "use Share → <b>Add to Home Screen</b> so you get an app icon.</p>"
                 f"<div style='background:#fff;display:inline-block;border-radius:8px'>{qr}</div>"
                 f"<div class='card mono'><a href='{e(phone_url)}' target=_blank>{e(phone_url)}</a></div>{board}"
                 "<p class=meta>Keep this link private: with it, someone could reach your login page. Your Mac must be on and awake.</p>"
                 "<details><summary class=meta>Optional: a ping when a clip is ready</summary>"
                 "<p class=meta>Install the free ntfy app, press +, and subscribe to:</p>"
                 f"<div class='card mono'>{e(notify.topic(db))}</div>"
                 "<form method=post action='/setup/notify-test'><div class=row><button class=reject>Send a test</button></div></form>"
                 "</details></div>")
        body = (f"{banner}<h2>Status</h2><div class=card><table class=checks>{rows}</table>"
                f"<p class=meta>Checked {_ago(at)} · <a href='/setup?refresh=1'>check again</a></p></div>"
                "<h2>Connect your accounts</h2><p class=meta>Paste a value and press Save. Leave a box empty to keep what's saved. "
                "Step-by-step help for each one is in SETUP.md.</p>"
                f"<div class=grid>{phone}{''.join(forms)}{links}</div>")
        return page("/setup", body, refresh=5 if flow.status == "waiting" else None)

    @app.post("/setup/notify-test", dependencies=write)
    def notify_test():
        link = notify.public_link("/")
        ok = notify.send(db, "GTA 6 Clipper", "Notifications work. Tap to open your dashboard.",
                         click=link, wait=True)
        return go("/setup", msg="Test notification sent. Check your phone." if ok
                  else "Couldn't send the notification (see the log). Check the internet connection.")

    @app.post("/setup/address", dependencies=write)
    def use_this_address(request: Request):
        host = request.headers.get("host", "")
        if request.headers.get("x-forwarded-proto") != "https" or not HOST_RE.match(host):
            return go("/setup", msg="Open the dashboard through its https address first.")
        orch.settings.update({"PUBLIC_BASE_URL": f"https://{host}"})
        reload_after_keys()
        return go("/setup", msg=f"The main address is now https://{host}")

    @app.post("/setup/tiktok-verify", dependencies=write)
    async def tiktok_verify(file: UploadFile = File(...)):
        name = re.sub(r" ?\(\d+\)(?=\.txt$)", "", Path(file.filename or "").name)  # "x (1).txt" from a re-download
        content = (await file.read(4097)).decode("utf-8", "replace").strip()
        if not VERIFY_FILE_RE.match(name) or not content or len(content) > 4096:
            return go("/setup", msg="That doesn't look like TikTok's verification file (a small .txt file).")
        db.kv_set("tiktok_verify", json.dumps({"name": name, "content": content}))
        return go("/setup", msg=f"Uploaded {name}. Now press Verify in TikTok's developer portal.")

    @app.post("/setup/keys", dependencies=write)
    async def save_keys(request: Request):
        form = await request.form()
        changed = await run_in_threadpool(orch.settings.update, {k: str(v) for k, v in form.items()})
        if "TIKTOK_REFRESH_TOKEN" in changed:
            db.kv_set("tiktok_token", "{}")  # forget the old TikTok session so the new token is used
        await run_in_threadpool(reload_after_keys)
        msg = f"Saved: {', '.join(changed)}" if changed else "Nothing changed."
        return go("/setup", msg=msg)

    @app.post("/connect/youtube", dependencies=write)
    def connect_youtube():
        yt_connector.start()
        return RedirectResponse("/setup", 303)

    @app.get("/connect/tiktok", dependencies=[Depends(auth)])
    def connect_tiktok():
        try:
            mode = ((orch.cfg.uploads.targets.get("tiktok") or {}).get("mode") or "inbox").lower()
            return RedirectResponse(tt_connector.authorize_url(direct=mode == "direct"), 302)
        except RuntimeError as ex:
            return go("/setup", msg=str(ex))

    @app.get("/connect/tiktok/callback", dependencies=[Depends(auth)])
    def tiktok_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
        if error or not code:
            return go("/setup", msg=f"TikTok said: {error_description or error or 'no code returned'}")
        try:
            tt_connector.finish(code, state)
        except (RuntimeError, ValueError) as ex:
            return go("/setup", msg=str(ex))
        return go("/setup", msg="TikTok connected.", refresh=1)

    # --- Settings: edit config.yaml in the browser ------------------------------
    @app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def settings_page(msg: str = "", ok: int = 1):
        text = orch.config_path.read_text(encoding="utf-8")
        banner = f"<div class=card><b class={'ok' if ok else 'bad'}>{e(msg)}</b></div>" if msg else ""
        body = (f"{banner}<h2>Settings (config.yaml)</h2><p class=meta>Streamers, permission, review mode, upload targets and "
                "detection tuning. Every option is explained in the comments. The bot picks up changes within a minute; "
                "if there's a typo, nothing is saved and you'll see the error here.</p>"
                f"<form method=post action='/settings'><textarea class=cfg name=text spellcheck=false>{e(text)}</textarea>"
                "<div class=row><button class=approve>Save settings</button></div></form>")
        return page("/settings", body)

    @app.post("/settings", dependencies=write)
    def save_settings(text: str = Form(...)):
        text = text.replace("\r\n", "\n")
        tmp = orch.cfg.data_dir / "config.check.yaml"
        tmp.write_text(text, encoding="utf-8")
        try:
            new = load_config(tmp)
            if not new.streamers:
                raise ValueError("the streamers list is empty")
        except Exception as ex:  # noqa: BLE001 - show any parse/validation error to the user
            return go("/settings", ok=0, msg=f"Not saved, fix this first: {str(ex)[:300]}")
        finally:
            tmp.unlink(missing_ok=True)
        # write in place (config.yaml is a single-file Docker bind mount, so no rename)
        with open(orch.config_path, "w", encoding="utf-8") as f:
            f.write(text)
        orch._maybe_reload()
        return go("/settings", msg="Saved. The bot is using the new settings.")

    @app.get("/api/status", dependencies=[Depends(auth)])
    def api_status():
        return JSONResponse(orch.status())

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # --- public pages (no login) -----------------------------------------------------
    def public_page(title: str, body: str) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)} · GTA 6 Clipper</title><style>{CSS}main{{max-width:760px}}</style></head><body>"
            f"<header><h1>GTA 6 Clipper</h1></header><main><h2>{e(title)}</h2>{body}</main></body></html>")

    def public_route(title: str, body: str):
        def handler():  # no parameters, so nothing from the URL reaches the page
            return public_page(title, body)
        return handler

    for slug, (title, body) in PUBLIC_PAGES.items():
        app.add_api_route(f"/{slug}", public_route(title, body), methods=["GET"], response_class=HTMLResponse)

    @app.get("/{name}")
    def verification_file(name: str):
        vf = json.loads(db.kv_get("tiktok_verify") or "{}")
        if not VERIFY_FILE_RE.match(name) or name != vf.get("name"):
            raise HTTPException(404)
        return PlainTextResponse(vf["content"])

    return app
