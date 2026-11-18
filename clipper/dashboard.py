"""Review dashboard: watch clips, edit titles, approve/reject, see who's live.

Protected by HTTP basic auth (DASHBOARD_USER / DASHBOARD_PASSWORD).
/public/<token>.mp4 is the only unauthenticated route (Metricool needs it to fetch videos),
and it only serves clips that were approved.
"""
from __future__ import annotations

import html
import logging
import os
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import db as dbm

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
"""


def create_app(orch) -> FastAPI:
    app = FastAPI(title="GTA 6 Clipper", docs_url=None, redoc_url=None)
    user = os.environ.get("DASHBOARD_USER", "admin")
    password = os.environ.get("DASHBOARD_PASSWORD")
    if not password:
        password = secrets.token_urlsafe(12)
        log.warning("DASHBOARD_PASSWORD not set; generated one for this run: %s", password)

    def auth(creds: HTTPBasicCredentials = Depends(security)):
        ok = secrets.compare_digest(creds.username, user) and secrets.compare_digest(creds.password, password)
        if not ok:
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    db: dbm.DB = orch.db

    def clip_card(c: dict, actions: str) -> str:
        sig = c.get("signals") or {}
        ups = c.get("uploads") or {}
        up_txt = " · ".join(
            f"{k}: " + (f"<a href='{e(v.get('url'))}' target=_blank>ok</a>" if v.get("ok") and v.get("url")
                        else ("ok" if v.get("ok") else f"<span class=bad>{e(v.get('error', '')[:80])}</span>"))
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
            f"<tr><td><b>{e(w['streamer'])}</b></td><td>{e(w['platform'])}</td><td><a href='{e(w['url'])}' target=_blank>{e(w['title'][:80])}</a></td>"
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
        perm_note = lambda c: review_form(c, f"<p class='meta warn'>{e(c['streamer'])} has not granted permission. Set <code>permission: granted</code> in config.yaml once they have, or approving will be blocked.</p>")  # noqa: E731
        body = f"""<header><h1>GTA 6 Clipper</h1>
<span class=pill>review mode: {'ON' if cfg.review_mode else 'OFF (auto-upload)'}</span>
<span class=pill>uploads to: {e(', '.join(st['uploaders']) or 'nothing configured')}</span>
<span class=pill>max {cfg.uploads.max_per_day}/day</span></header><main>
<h2>Live now</h2><div class=card><table><tr><th>Streamer</th><th>Platform</th><th>Title</th><th>Viewers</th><th>Since</th><th>Clips</th><th>Score now</th></tr>{live_rows}</table></div>
<h2>Waiting for your review ({len(ready)})</h2>{cards(ready, lambda c: clip_card(c, review_form(c)))}
<h2>Blocked: no permission from streamer ({len(needs)})</h2>{cards(needs, lambda c: clip_card(c, perm_note(c)))}
<h2>Uploaded / queued</h2>{cards(done, lambda c: clip_card(c, ''))}
<h2>Failed</h2>{cards(failed, retry)}
<h2>AI said skip (low score)</h2>{cards(low, lambda c: clip_card(c, review_form(c)))}
<h2>Your streamer list</h2><div class=card><table><tr><th>Name</th><th>Enabled</th><th>Permission</th><th>Last check</th></tr>{check_rows}</table></div>
<h2>Other big GTA 6 streams on Twitch right now</h2><div class=card><table><tr><th>Channel</th><th>Viewers</th><th>Lang</th><th>Title</th><th>Seen</th></tr>{disc_rows}</table></div>
</main>"""
        return f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>GTA 6 Clipper</title><style>{CSS}</style><meta http-equiv=refresh content=120></head><body>{body}</body></html>"

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

    @app.post("/clips/{clip_id}/approve", dependencies=[Depends(auth)])
    def approve(clip_id: int, title: str = Form(...), description: str = Form("")):
        c = db.get_clip(clip_id)
        if not c:
            raise HTTPException(404)
        s = orch.streamer_by_name(c["streamer"])
        if orch.cfg.require_permission_to_upload and not (s and s.upload_allowed):
            db.update_clip(clip_id, title=title, description=description, status=dbm.NEEDS_PERMISSION)
            return HTMLResponse(f"<p>{e(c['streamer'])} has not granted permission (config.yaml). Not uploading. <a href='/'>Back</a></p>", 403)
        db.update_clip(clip_id, title=title.strip(), description=description, status=dbm.APPROVED)
        return RedirectResponse("/", 303)

    @app.post("/clips/{clip_id}/reject", dependencies=[Depends(auth)])
    def reject(clip_id: int):
        db.update_clip(clip_id, status=dbm.REJECTED)
        return RedirectResponse("/", 303)

    @app.post("/clips/{clip_id}/retry", dependencies=[Depends(auth)])
    def retry(clip_id: int):
        db.update_clip(clip_id, status=dbm.APPROVED, error=None)
        return RedirectResponse("/", 303)

    @app.get("/api/status", dependencies=[Depends(auth)])
    def api_status():
        return JSONResponse(orch.status())

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app
