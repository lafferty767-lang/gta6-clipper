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
.meta{font-size:13px;color:var(--muted)}.score{font-weight:700;color:var(--accent)}.warn{color:var(--warn)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
a{color:#8ab4ff}
nav{display:flex;gap:14px;margin-left:auto}nav a{color:var(--text);text-decoration:none;padding:6px 10px;border-radius:8px}nav a.on{background:var(--line)}
.checks td:first-child{width:28px}.st-ok{color:var(--ok)}.st-warn{color:var(--warn)}.st-fail{color:var(--bad)}.st-off{color:var(--muted)}
.code{font:700 28px/1.2 ui-monospace,monospace;letter-spacing:.12em;background:#0f1117;padding:10px 16px;border-radius:10px;display:inline-block;margin:6px 0}
.mono{font-family:ui-monospace,monospace;font-size:13px;word-break:break-all}textarea.cfg{min-height:70vh;font:13px/1.45 ui-monospace,monospace}
.ok{color:var(--ok)}.btn{display:inline-block;background:var(--accent);color:#fff;border-radius:8px;padding:9px 14px;font-weight:600;text-decoration:none}
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
        if origin and urlparse(origin).netloc != request.headers.get("host"):
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
            f"<title>GTA 6 Clipper</title><style>{CSS}</style>{meta}</head><body>"
            f"<header><h1>GTA 6 Clipper</h1><nav>{nav}</nav></header><main>{body}</main></body></html>")

    def clip_card(c: dict, actions: str) -> str:
        sig = c.get("signals") or {}
        ups = c.get("uploads") or {}
        up_txt = " · ".join(
            f"{k}: " + (f"<a href='{e(v.get('url'))}' target=_blank>ok</a>" if v.get("ok") and v.get("url")
                        else ("ok" if v.get("ok") else f"<span class=bad>{e(str(v.get('error') or '')[:80])}</span>"))
            for k, v in ups.items() if isinstance(v, dict))
        return f"""<div class=card>
<video controls preload=metadata src="/media/{c['id']}"></video>
<div class=meta>#{c['id']} · <b>{e(c['streamer'])}</b> on {e(c['platform'])} · {_ago(c.get('moment_at'))}
 · signal <span class=score>{e(c.get('score'))}</span>{' · AI <span class=score>' + e(c['llm_score']) + '/10</span>' if c.get('llm_score') is not None else ''}</div>
<div class=meta>chat z {e(sig.get('chat_z'))} · hype z {e(sig.get('hype_z'))} · audio z {e(sig.get('audio_z'))} · viewer clips {e(sig.get('viewer_clips'))}</div>
{f"<div class=meta>{e(c.get('llm_reason'))}</div>" if c.get('llm_reason') else ''}
{f"<div class=meta>{up_txt}</div>" if up_txt else ''}
{f"<div class='meta bad'>{e(c.get('error'))}</div>" if c.get('error') else ''}
{actions}</div>"""

    def review_form(c: dict, note: str = "") -> str:
        return f"""<form method=post action="/clips/{c['id']}/approve">
<label>Title</label><input name=title value="{e(c.get('title'))}" maxlength=100>
<label>Description</label><textarea name=description>{e(c.get('description'))}</textarea>
{note}<div class=row><button class=approve>Approve &amp; upload</button>
<button class=reject formaction="/clips/{c['id']}/reject">Reject</button></div></form>"""

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def index():
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
        ready = db.list_clips([dbm.READY], 50)
        needs = db.list_clips([dbm.NEEDS_PERMISSION], 30)
        low = db.list_clips([dbm.LOW_SCORE], 12)
        failed = db.list_clips([dbm.FAILED], 12)
        done = db.list_clips([dbm.UPLOADED, dbm.UPLOADING, dbm.APPROVED], 24)
        disc = db.list_discovered(6)
        listed = {h.lower() for s in cfg.streamers for h in s.platforms.values()}
        disc_rows = "".join(
            f"<tr><td><a href='https://twitch.tv/{e(d['login'])}' target=_blank>{e(d['display_name'])}</a>{' ✓ on your list' if d['login'].lower() in listed else ''}</td>"
            f"<td>{e(d['viewers'])}</td><td>{e(d['language'])}</td><td>{e((d['title'] or '')[:90])}</td><td>{_ago(d['last_seen'])}</td></tr>"
            for d in disc) or "<tr><td colspan=5 class=meta>Needs Twitch API keys.</td></tr>"

        def cards(rows, fn):
            return "<div class=grid>" + "".join(fn(c) for c in rows) + "</div>" if rows else "<p class=meta>None.</p>"

        retry = lambda c: clip_card(c, f"<form method=post action='/clips/{c['id']}/retry'><div class=row><button class=retry>Retry upload</button></div></form>")  # noqa: E731
        perm_note = lambda c: review_form(c, f"<p class='meta warn'>{e(c['streamer'])} has not granted permission. Once they have, set <code>permission: granted</code> on the <a href='/settings'>Settings</a> page; until then approving is blocked.</p>")  # noqa: E731
        body = f"""<div class=row><span class=pill>review mode: {'ON' if cfg.review_mode else 'OFF (auto-upload)'}</span>
<span class=pill>uploads to: {e(', '.join(st['uploaders']) or 'nothing set up yet, see Setup')}</span>
<span class=pill>max {cfg.uploads.max_per_day}/day</span></div>
<h2>Live now</h2><div class=card><table><tr><th>Streamer</th><th>Platform</th><th>Title</th><th>Viewers</th><th>Since</th><th>Clips</th><th>Score now</th></tr>{live_rows}</table></div>
<h2>Waiting for your review ({len(ready)})</h2>{cards(ready, lambda c: clip_card(c, review_form(c)))}
<h2>Blocked: no permission from streamer ({len(needs)})</h2>{cards(needs, lambda c: clip_card(c, perm_note(c)))}
<h2>Uploaded / queued</h2>{cards(done, lambda c: clip_card(c, ''))}
<h2>Failed</h2>{cards(failed, retry)}
<h2>AI said skip (low score)</h2>{cards(low, lambda c: clip_card(c, review_form(c)))}
<h2>Your streamer list</h2><div class=card><table><tr><th>Name</th><th>Enabled</th><th>Permission</th><th>Last check</th></tr>{check_rows}</table></div>
<h2>Other big GTA 6 streams on Twitch right now</h2><div class=card><table><tr><th>Channel</th><th>Viewers</th><th>Lang</th><th>Title</th><th>Seen</th></tr>{disc_rows}</table></div>
"""
        return page("/", body, refresh=120)

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
    def approve(clip_id: int, title: str = Form(...), description: str = Form("")):
        c = db.get_clip(clip_id)
        if not c:
            raise HTTPException(404)
        s = orch.streamer_by_name(c["streamer"])
        if orch.cfg.require_permission_to_upload and not (s and s.upload_allowed):
            db.update_clip(clip_id, title=title, description=description, status=dbm.NEEDS_PERMISSION)
            return HTMLResponse(f"<p>{e(c['streamer'])} has not granted permission (see Settings). Not uploading. <a href='/'>Back</a></p>", 403)
        db.update_clip(clip_id, title=title.strip(), description=description, status=dbm.APPROVED)
        return RedirectResponse("/", 303)

    @app.post("/clips/{clip_id}/reject", dependencies=write)
    def reject(clip_id: int):
        db.update_clip(clip_id, status=dbm.REJECTED)
        return RedirectResponse("/", 303)

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
        body = (f"{banner}<h2>Status</h2><div class=card><table class=checks>{rows}</table>"
                f"<p class=meta>Checked {_ago(at)} · <a href='/setup?refresh=1'>check again</a></p></div>"
                "<h2>Connect your accounts</h2><p class=meta>Paste a value and press Save. Leave a box empty to keep what's saved. "
                "Step-by-step help for each one is in SETUP.md.</p>"
                f"<div class=grid>{''.join(forms)}{links}</div>")
        return page("/setup", body, refresh=5 if flow.status == "waiting" else None)

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
