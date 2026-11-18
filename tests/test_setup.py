import json
import os
import shutil
import stat
import time
from pathlib import Path
from urllib.parse import unquote_plus

import pytest
from fastapi.testclient import TestClient

from clipper import connect
from clipper.settings import ALL_KEYS, Settings

ROOT = Path(__file__).resolve().parents[1]
AUTH = ("admin", "pw")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ALL_KEYS | {"PUBLIC_BASE_URL"}:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")


@pytest.fixture
def app(tmp_path, monkeypatch):
    from clipper.dashboard import create_app
    from clipper.orchestrator import Orchestrator

    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(tmp_path / "data"))
    orch = Orchestrator(str(cfg))
    return orch, TestClient(create_app(orch))


def test_settings_store(tmp_path, monkeypatch):
    s = Settings(tmp_path)
    changed = s.update({"TWITCH_CLIENT_ID": " abc ", "NOT_A_KEY": "x", "ANTHROPIC_API_KEY": ""})
    assert changed == ["TWITCH_CLIENT_ID"]
    assert os.environ["TWITCH_CLIENT_ID"] == "abc"
    f = tmp_path / "secrets.json"
    assert json.loads(f.read_text()) == {"TWITCH_CLIENT_ID": "abc"}
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    monkeypatch.delenv("TWITCH_CLIENT_ID")
    Settings(tmp_path)  # reload applies saved values to the environment again
    assert os.environ["TWITCH_CLIENT_ID"] == "abc"


def test_setup_page_and_save_keys(app):
    orch, c = app
    assert c.get("/setup").status_code == 401
    r = c.get("/setup", auth=AUTH)
    assert r.status_code == 200 and "Connect YouTube" in r.text and "Twitch" in r.text
    assert not orch.twitch.configured
    r = c.post("/setup/keys", data={"TWITCH_CLIENT_ID": "id1", "TWITCH_CLIENT_SECRET": "sec1"}, auth=AUTH,
               follow_redirects=False)
    assert r.status_code == 303
    assert orch.twitch.configured  # clients rebuilt with the new keys
    assert "saved:" in c.get("/setup", auth=AUTH).text


def test_cross_site_post_is_blocked(app):
    _, c = app
    r = c.post("/setup/keys", data={"TWITCH_CLIENT_ID": "x"}, auth=AUTH,
               headers={"Origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403


def test_settings_editor_validates(app):
    orch, c = app
    original = orch.config_path.read_text()
    r = c.post("/settings", data={"text": "streamers: [oops"}, auth=AUTH, follow_redirects=False)
    assert r.status_code == 303 and "ok=0" in r.headers["location"]
    assert orch.config_path.read_text() == original
    new = original.replace("review_mode: true", "review_mode: false")
    r = c.post("/settings", data={"text": new}, auth=AUTH, follow_redirects=False)
    assert r.status_code == 303 and "ok=0" not in r.headers["location"]
    assert orch.cfg.review_mode is False


def test_tiktok_connect_needs_keys_and_https(app, monkeypatch):
    _, c = app
    r = c.get("/connect/tiktok", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303 and "/setup" in r.headers["location"]
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "ck")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://1-2-3-4.sslip.io")
    r = c.get("/connect/tiktok", auth=AUTH, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(connect.TIKTOK_AUTH_URL)
    assert "redirect_uri=https%3A%2F%2F1-2-3-4.sslip.io%2Fconnect%2Ftiktok%2Fcallback" in r.headers["location"]
    # a callback with an unknown state is refused
    r = c.get("/connect/tiktok/callback?code=abc&state=forged", auth=AUTH, follow_redirects=False)
    assert "expired" in r.headers["location"]


class FakeResp:
    def __init__(self, status, payload):
        self.status_code, self._p, self.text = status, payload, json.dumps(payload)

    def json(self):
        return self._p


def test_youtube_device_flow(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cid")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "sec")
    calls = {"token": 0}

    def fake_post(url, data=None, **kw):
        if url == connect.GOOGLE_DEVICE_URL:
            assert data["scope"] == " ".join(connect.YOUTUBE_DEVICE_SCOPES)
            return FakeResp(200, {"device_code": "dc", "user_code": "ABC-DEF", "verification_url": "https://www.google.com/device",
                                  "expires_in": 60, "interval": 0})
        calls["token"] += 1
        if calls["token"] < 2:
            return FakeResp(428, {"error": "authorization_pending"})
        return FakeResp(200, {"access_token": "at", "refresh_token": "rt-123"})

    monkeypatch.setattr(connect.httpx, "post", fake_post)
    got = []
    yt = connect.YouTubeDeviceConnector(got.append)
    flow = yt.start()
    assert flow.status == "waiting" and flow.user_code == "ABC-DEF"
    for _ in range(50):
        if flow.status != "waiting":
            break
        time.sleep(0.05)
    assert flow.status == "done" and got == ["rt-123"]


def test_public_pages_need_no_login(app):
    _, c = app
    for path in ("/about", "/privacy", "/terms"):
        r = c.get(path)
        assert r.status_code == 200 and "GTA 6 Clipper" in r.text
    r = c.get("/privacy", params={"b": "<script>x</script>", "title": "<script>"})
    assert "<script>" not in r.text  # nothing from the URL is put on the page
    assert c.get("/nothing.txt").status_code == 404


def test_tiktok_verification_file(app):
    _, c = app
    name = "tiktokAbC123xyz.txt"
    content = "tiktok-developers-site-verification=AbC123xyz"
    files = {"file": (name, content.encode(), "text/plain")}
    assert c.post("/setup/tiktok-verify", files=files).status_code == 401
    r = c.post("/setup/tiktok-verify", files=files, auth=AUTH, follow_redirects=False)
    assert r.status_code == 303 and "Uploaded" in r.headers["location"]
    r = c.get(f"/{name}")  # public, so TikTok can fetch it
    assert r.status_code == 200 and r.text == content
    assert c.get("/other.txt").status_code == 404
    bad = {"file": ("../../etc/passwd", b"x", "text/plain")}
    r = c.post("/setup/tiktok-verify", files=bad, auth=AUTH, follow_redirects=False)
    assert "doesn't look like" in unquote_plus(r.headers["location"])
    assert "Serving" in c.get("/setup", auth=AUTH).text


def test_use_this_address(app, monkeypatch):
    orch, c = app
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://1-2-3-4.sslip.io")
    orch.reload_integrations()
    alt = {"host": "1-2-3-4.nip.io", "x-forwarded-proto": "https"}
    assert "as the main address" in c.get("/setup", auth=AUTH, headers=alt).text
    r = c.post("/setup/address", auth=AUTH, follow_redirects=False)  # plain http: refused
    assert "https address first" in unquote_plus(r.headers["location"])
    r = c.post("/setup/address", auth=AUTH, headers=alt, follow_redirects=False)
    assert r.status_code == 303
    assert orch.public_base_url == "https://1-2-3-4.nip.io"
    assert os.environ["PUBLIC_BASE_URL"] == "https://1-2-3-4.nip.io"
    assert "as the main address" not in c.get("/setup", auth=AUTH, headers=alt).text


def test_doctor_metricool(monkeypatch, tmp_path):
    from clipper import doctor

    class Cfg:
        class uploads:
            targets = {"metricool": {"enabled": True}}

    for k, v in {"METRICOOL_USER_TOKEN": "tok", "METRICOOL_USER_ID": "7", "METRICOOL_BLOG_ID": "42"}.items():
        monkeypatch.setenv(k, v)
    replies = {}
    monkeypatch.setattr(doctor.httpx, "get", lambda url, **kw: replies["r"])
    replies["r"] = FakeResp(200, [{"id": 42, "label": "GTA clips"}])
    assert doctor._metricool(Cfg, "https://x.sslip.io").status == doctor.OK
    replies["r"] = FakeResp(200, [{"id": 9}])
    assert doctor._metricool(Cfg, "https://x.sslip.io").status == doctor.FAIL
    replies["r"] = FakeResp(403, {"error": "denied"})
    assert doctor._metricool(Cfg, "https://x.sslip.io").status == doctor.FAIL
    replies["r"] = FakeResp(200, {"something": "else"})  # unknown shape: don't guess
    assert doctor._metricool(Cfg, "https://x.sslip.io").status == doctor.OK
