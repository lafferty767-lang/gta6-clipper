"""Regression tests for the upload cap, interrupted uploads, the YouTube health check and the
dashboard's unauthenticated surface."""
import json
import shutil
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from clipper import db as dbm
from clipper import doctor

ROOT = Path(__file__).resolve().parents[1]


class FakeResp:
    def __init__(self, status, payload):
        self.status_code, self._p, self.text = status, payload, json.dumps(payload)

    def json(self):
        return self._p


def test_partly_failed_clips_count_toward_cap_and_spacing(tmp_path):
    db = dbm.DB(tmp_path / "t.db")
    now = time.time()
    # Reached YouTube, refused by TikTok: the clip is FAILED but it was posted publicly.
    db.add_clip(streamer="a", platform="twitch", channel="a", status=dbm.FAILED,
                uploads={"youtube": {"ok": True, "at": now - 60}, "tiktok": {"ok": False, "error": "x"}})
    # Only exported to the local folder: not a public post.
    db.add_clip(streamer="a", platform="twitch", channel="a", status=dbm.UPLOADED, uploads={"local": {"ok": True, "at": now - 30}})
    # Posted yesterday.
    db.add_clip(streamer="a", platform="twitch", channel="a", status=dbm.UPLOADED, uploads={"youtube": {"ok": True, "at": now - 90000}})
    assert db.uploads_since(now - 86400) == 1
    assert db.last_upload_at() == pytest.approx(now - 60)


def test_restart_during_upload_offers_retry(tmp_path):
    db = dbm.DB(tmp_path / "t.db")
    stuck = db.add_clip(streamer="a", platform="twitch", channel="a", status=dbm.UPLOADING)
    done = db.add_clip(streamer="a", platform="twitch", channel="a", status=dbm.UPLOADED)
    assert db.reset_interrupted_uploads() == 1
    assert db.get_clip(stuck)["status"] == dbm.FAILED
    assert "Retry" in db.get_clip(stuck)["error"]
    assert db.get_clip(done)["status"] == dbm.UPLOADED


class YtCfg:
    class uploads:
        targets = {"youtube": {"enabled": True}}


@pytest.fixture
def yt_env(monkeypatch):
    for k, v in {"YOUTUBE_CLIENT_ID": "cid", "YOUTUBE_CLIENT_SECRET": "sec", "YOUTUBE_REFRESH_TOKEN": "rt"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(doctor.httpx, "post", lambda *a, **kw: FakeResp(200, {"access_token": "at"}))


@pytest.mark.parametrize("status,body,expected,words", [
    (200, {"items": [{"snippet": {"title": "My GTA"}}]}, doctor.OK, "My GTA"),
    (403, {"error": {"code": 403, "message": "YouTube Data API v3 has not been used",
                     "errors": [{"reason": "accessNotConfigured"}]}}, doctor.FAIL, "isn't turned on"),
    (403, {"error": {"code": 403, "details": [{"reason": "SERVICE_DISABLED"}]}}, doctor.FAIL, "isn't turned on"),
    (403, {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}, doctor.WARN, "quota"),
    (500, {"error": {"code": 500, "message": "backend"}}, doctor.WARN, "500"),
])
def test_youtube_check_reads_google_errors(monkeypatch, yt_env, status, body, expected, words):
    monkeypatch.setattr(doctor.httpx, "get", lambda *a, **kw: FakeResp(status, body))
    c = doctor._youtube(YtCfg)
    assert c.status == expected and words in c.detail


def test_youtube_check_network_error_is_a_warning(monkeypatch, yt_env):
    def boom(*a, **kw):
        raise httpx.ConnectError("dns")
    monkeypatch.setattr(doctor.httpx, "get", boom)
    assert doctor._youtube(YtCfg).status == doctor.WARN


def test_crashed_check_keeps_its_name(monkeypatch, tmp_path):
    def boom(*a, **kw):
        raise RuntimeError("kaput")
    monkeypatch.setattr(doctor, "_youtube", boom)
    from clipper.config import load_config
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg_path)
    monkeypatch.setattr(doctor, "_public_url", lambda url: doctor.Check("Public HTTPS address", doctor.OFF, "skip"))
    monkeypatch.setattr(doctor, "_twitch", lambda: doctor.Check("Twitch", doctor.OFF, "skip"))
    monkeypatch.setattr(doctor, "_anthropic", lambda cfg: doctor.Check("Claude (AI titles)", doctor.OFF, "skip"))
    monkeypatch.setattr(doctor, "_tiktok", lambda cfg, db: doctor.Check("TikTok", doctor.OFF, "skip"))
    monkeypatch.setattr(doctor, "_metricool", lambda cfg, url: doctor.Check("Metricool", doctor.OFF, "skip"))
    monkeypatch.setattr(doctor, "_streamers", lambda cfg: doctor.Check("Streamers", doctor.OFF, "skip"))
    rows = {c.name: c for c in doctor.run_checks(load_config(cfg_path), None)}
    assert "kaput" in rows["YouTube Shorts"].detail
    assert not any("lambda" in name for name in rows)


def test_api_schema_is_not_public(tmp_path, monkeypatch):
    from clipper.dashboard import create_app
    from clipper.orchestrator import Orchestrator

    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(tmp_path / "data"))
    c = TestClient(create_app(Orchestrator(str(cfg))))
    assert c.get("/openapi.json").status_code in (401, 404)
    assert c.get("/docs").status_code in (401, 404)
