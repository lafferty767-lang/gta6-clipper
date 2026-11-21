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


def test_tunnel_address_becomes_public_address(monkeypatch):
    from clipper import orchestrator

    class Orch:
        reloads = 0

        def reload_integrations(self):
            Orch.reloads += 1

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://old.trycloudflare.com")
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(200, {"hostname": "new-name.trycloudflare.com"}))
    w = orchestrator.TunnelWatcher(Orch(), "http://tunnel:2000/quicktunnel")
    w.check()
    w.check()  # unchanged the second time: no extra reload
    import os
    assert os.environ["PUBLIC_BASE_URL"] == "https://new-name.trycloudflare.com"
    assert Orch.reloads == 1

    def down(*a, **kw):
        raise httpx.ConnectError("tunnel not up yet")
    monkeypatch.setattr(httpx, "get", down)
    w.check()  # no crash, address kept
    assert os.environ["PUBLIC_BASE_URL"] == "https://new-name.trycloudflare.com"


def test_tiktok_login_survives_tunnel_address_change(monkeypatch):
    from clipper import connect
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "k")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "s")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://first.trycloudflare.com")
    got = {}
    c = connect.TikTokConnector(lambda tokens: got.update(tokens))
    url = c.authorize_url()
    state = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))["state"]
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://second.trycloudflare.com")
    sent = {}

    def post(url, data=None, **kw):
        sent.update(data)
        return FakeResp(200, {"refresh_token": "r", "access_token": "a"})
    monkeypatch.setattr(connect.httpx, "post", post)
    c.finish("code", state)
    assert sent["redirect_uri"] == "https://first.trycloudflare.com/connect/tiktok/callback"
    assert got["refresh_token"] == "r"


def test_crashed_page_shows_the_error_only_when_signed_in(tmp_path, monkeypatch):
    from clipper.dashboard import create_app
    from clipper.orchestrator import Orchestrator

    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(tmp_path / "data"))
    orch = Orchestrator(str(cfg))
    c = TestClient(create_app(orch), raise_server_exceptions=False)
    assert c.get("/", auth=("admin", "pw")).status_code == 200

    def boom():
        raise KeyError("kaput")
    orch.status = boom
    r = c.get("/", auth=("admin", "pw"))
    assert r.status_code == 500 and "kaput" in r.text


def test_clips_page_handles_missing_titles_and_errors(tmp_path, monkeypatch):
    from clipper.dashboard import create_app
    from clipper.orchestrator import Orchestrator

    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(tmp_path / "data"))
    orch = Orchestrator(str(cfg))
    orch.db.add_clip(streamer="a", platform="twitch", channel="a", status=dbm.FAILED,
                     uploads={"tiktok": {"ok": False, "error": None}})
    orch.db.upsert_discovered("twitch", "foo", "Foo", 10, None, "en")
    orch.status = lambda: {"workers": [{"streamer": "a", "platform": "youtube", "url": "u", "title": None,
                                        "viewers": 1, "since": time.time(), "clips": 0, "score": {}}],
                           "checks": {}, "review_mode": True, "uploaders": []}
    c = TestClient(create_app(orch))
    assert c.get("/", auth=("admin", "pw")).status_code == 200


def test_clip_that_broke_before_rendering_has_no_player(tmp_path, monkeypatch):
    from clipper.dashboard import create_app
    from clipper.orchestrator import Orchestrator

    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(tmp_path / "data"))
    orch = Orchestrator(str(cfg))
    orch.db.add_clip(streamer="a", platform="twitch", channel="a", status=dbm.FAILED, error="cut: ffmpeg said no")
    html = TestClient(create_app(orch)).get("/", auth=("admin", "pw")).text
    assert "No video" in html and "ffmpeg said no" in html
    assert "<video" not in html and "Retry upload" not in html


def test_whisper_gets_samples_not_a_file_name(tmp_path, monkeypatch):
    import subprocess
    import numpy as np
    from clipper import transcribe as tr
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-ac", "2", "-ar", "44100", str(src)], check=True)
    wav = tr.extract_wav(src, 0.5, 1.0, tmp_path / "a.wav")
    samples = tr.load_wav(wav)
    assert samples.dtype == np.float32 and 15000 < len(samples) < 17000 and 0.05 < abs(samples).max() <= 1

    class Model:
        def transcribe(self, audio, **kw):
            assert not isinstance(audio, (str, Path))
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
    monkeypatch.setattr(tr, "_get_model", lambda cfg: Model())
    assert tr.transcribe(wav, None) == ("", [])  # a whisper failure no longer kills the clip


def _stream_like_stack(seed=0):
    """Gameplay that changes every frame, with a calm 'webcam' rectangle where a small blob moves."""
    import numpy as np
    rng = np.random.default_rng(seed)
    room = rng.integers(40, 200, (60, 70)).astype(np.float32)
    frames = []
    for i in range(30):
        f = rng.integers(0, 255, (180, 320)).astype(np.float32)
        cam = room.copy()
        cam[20:35, 25 + i % 10:40 + i % 10] = 255 if i % 2 else 0   # the streamer moving a little
        f[70:130, 0:70] = cam
        frames.append(f)
    return np.stack(frames)


def test_finds_the_webcam_box():
    from clipper import facecam
    box = facecam.find_box(_stream_like_stack(), faces=[(0.1, 0.45, 0.05, 0.1)])
    assert box is not None
    assert box.x == pytest.approx(0, abs=0.02) and box.y == pytest.approx(70 / 180, abs=0.02)
    assert box.w == pytest.approx(70 / 320, abs=0.02) and box.h == pytest.approx(60 / 180, abs=0.02)


def test_no_webcam_means_no_box():
    import numpy as np
    from clipper import facecam
    noise = np.random.default_rng(1).integers(0, 255, (30, 180, 320)).astype(np.float32)
    assert facecam.find_box(noise, faces=[]) is None
    # ...unless one face sits in the same place the whole time: frame the area around it
    faces = [(0.7, 0.1, 0.05, 0.09)] * 20
    box = facecam.find_box(noise, faces=faces)
    assert box and box.x < 0.725 < box.x + box.w and box.y < 0.145 < box.y + box.h


def test_split_layout_renders_streamer_on_top(tmp_path):
    import subprocess
    from clipper.config import Edit, Facecam
    from clipper.editor import probe_size, render_vertical
    from clipper.transcribe import Word
    src = tmp_path / "raw.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=3",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    out = render_vertical(src, 0.5, 2, tmp_path / "final.mp4", Edit(), Facecam(0.0, 0.4, 0.2, 0.28),
                          [Word(0.1, 0.5, "no"), Word(0.5, 0.9, "way")], "someone live on twitch.tv/someone")
    assert probe_size(out) == (1080, 1920)
