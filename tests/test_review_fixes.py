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
        db = None

        def reload_integrations(self):
            Orch.reloads += 1

    posted = []
    monkeypatch.setattr(orchestrator.notify, "publish_address", lambda db, a: posted.append(a) or True)

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://old.trycloudflare.com")
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(200, {"hostname": "new-name.trycloudflare.com"}))
    w = orchestrator.TunnelWatcher(Orch(), "http://tunnel:2000/quicktunnel")
    w.check()
    w.check()  # unchanged the second time: no extra reload
    import os
    assert os.environ["PUBLIC_BASE_URL"] == "https://new-name.trycloudflare.com"
    assert Orch.reloads == 1
    assert posted == ["https://new-name.trycloudflare.com"]  # posted for the phone link once, not every check

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


def _app(tmp_path, monkeypatch):
    from clipper.dashboard import create_app
    from clipper.orchestrator import Orchestrator
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    cfg = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", cfg)
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(tmp_path / "data"))
    orch = Orchestrator(str(cfg))
    return orch, TestClient(create_app(orch))


def test_phone_review_page_and_setup_link(tmp_path, monkeypatch):
    orch, c = _app(tmp_path, monkeypatch)
    a = orch.db.add_clip(streamer="Kai Cenat", platform="twitch", channel="kaicenat", status=dbm.READY, title="t1")
    b = orch.db.add_clip(streamer="Kai Cenat", platform="twitch", channel="kaicenat", status=dbm.READY, title="t2")
    page = c.get(f"/clip/{a}", auth=("admin", "pw")).text
    assert "Approve" in page and f"/clip/{b}" in page and "1 left" in page
    r = c.post(f"/clips/{a}/reject", data={"back": f"/clip/{a}"}, auth=("admin", "pw"), follow_redirects=False)
    assert r.headers["location"] == f"/clip/{a}"
    r = c.post(f"/clips/{b}/reject", data={"back": "https://evil.example/"}, auth=("admin", "pw"), follow_redirects=False)
    assert r.headers["location"] == "/"
    setup = c.get("/setup", auth=("admin", "pw")).text
    assert "github.io/gta6-clipper/go.html#gta6addr-" in setup and "<svg" in setup


def test_streamers_without_permission_are_not_recorded(tmp_path, monkeypatch):
    from clipper.platforms.base import LiveInfo
    orch, _ = _app(tmp_path, monkeypatch)
    started = []
    monkeypatch.setattr(orch, "discover", lambda: None)
    monkeypatch.setattr(orch, "check_live", lambda: {
        "Kai Cenat": LiveInfo(platform="twitch", channel="kaicenat", is_live=True, title="GTA 6", viewers=5, url="u"),
        "CaseOh": LiveInfo(platform="twitch", channel="caseoh_", is_live=True, title="GTA 6", viewers=9, url="u")})
    import clipper.orchestrator as o

    class FakeWorker:
        def __init__(self, cfg, db, s, info, *a):
            self.name, self.info, self.done = s.name, info, False
        def start(self):
            started.append(self.name)
    monkeypatch.setattr(o, "StreamWorker", FakeWorker)
    for s in orch.cfg.streamers:
        if s.name == "Kai Cenat":
            s.permission = "granted"
    monkeypatch.setattr(orch, "_maybe_reload", lambda: None)
    orch.step()
    assert started == ["Kai Cenat"]


def test_no_thread_hides_threading_internals():
    # A worker stored its stop flag as self._stop, which hides threading.Thread's own _stop(): once the
    # thread ended, is_alive() crashed with "'Event' object is not callable" and so did the Clips page.
    import re
    for f in (ROOT / "clipper").rglob("*.py"):
        assert not re.search(r"self\._(stop|started|is_stopped|tstate_lock)\s*=", f.read_text()), f


def test_viral_meter_ranks_big_shared_moments_first():
    from clipper import viral
    pool = [6, 6.5, 7, 7.5, 8, 9, 10, 12]
    big = {"score": 12, "signals": {"chat_z": 6, "hype_z": 3, "audio_z": 5, "viewer_clips": 2}, "transcript": "x" * 200}
    meh = {"score": 6.5, "signals": {"chat_z": 0.5, "hype_z": 0, "audio_z": 3, "viewer_clips": 0}, "transcript": ""}
    assert viral.score(big, pool) >= 85 and viral.label(viral.score(big, pool))[0] == "Post it"
    assert viral.score(meh, pool) < 50
    assert 0 <= viral.score({"score": None, "signals": None}, []) <= 100
    assert viral.score({**meh, "llm_score": 10}, pool) > viral.score(meh, pool)


@pytest.mark.parametrize("text,ok", [
    ("Kai Cenat can't believe this GTA 6 chase 💀", True),
    ("Lacy said what the fuck 😭", False), ("FUUUCK no way", False), ("this is sh*t", False),
    ("bro WTF", False), ("class act from Clix", True), ("hello chat", True), ("Shitake mushrooms", False),
    ("assassination mission gone wrong", True), ("he called him a dumbass", False), ("Scunthorpe", True),
])
def test_clean_titles(text, ok):
    from clipper import clean
    assert clean.is_clean(text) is ok


def test_template_title_skips_sweary_quotes():
    from clipper import metadata
    from clipper.config import Streamer
    s = Streamer(name="Lacy", platforms={"twitch": "lacy"})
    m = metadata.template_meta(s, "twitch", "lacy", "What the fuck was that. Bro just stole a plane. Lets go")
    assert m.title == 'Lacy in GTA 6: "Bro just stole a plane" 💀'
    m = metadata.template_meta(s, "twitch", "lacy", "Holy shit. Fuck")
    assert m.title in [t.format(n="Lacy") for t in metadata.clean.FALLBACKS]
    assert all(metadata.clean.is_clean(h.lstrip("#")) for h in m.hashtags)


def test_daily_cap_still_lets_a_half_posted_clip_finish(tmp_path, monkeypatch):
    """A clip already on YouTube whose TikTok step was cut off was counted when it went out, so
    the daily limit must not hold back its TikTok copy."""
    import types
    from clipper import orchestrator
    db = dbm.DB(tmp_path / "t.db")
    now = time.time()
    for i in range(5):     # limit reached, last post 30 minutes ago
        db.add_clip(streamer="a", platform="twitch", channel="a", status=dbm.UPLOADED,
                    uploads={"youtube": {"ok": True, "at": now - 1800 - i * 1300}})
    half = db.add_clip(streamer="a", platform="twitch", channel="a", status=dbm.APPROVED, final_path="/x.mp4",
                       uploads={"youtube": {"ok": True, "at": now - 7000}})
    fresh = db.add_clip(streamer="a", platform="twitch", channel="a", status=dbm.APPROVED, final_path="/y.mp4")
    sent = []

    class Up:
        def __init__(self, name):
            self.name = name

        def upload(self, clip, path):
            sent.append((clip["id"], self.name))
            return {}
    cfg = types.SimpleNamespace(uploads=types.SimpleNamespace(max_per_day=5, min_minutes_between=20),
                                require_permission_to_upload=False)
    orch = types.SimpleNamespace(cfg=cfg, db=db, uploaders=[Up("youtube"), Up("tiktok")],
                                 streamer_by_name=lambda n: None)
    monkeypatch.setattr(orchestrator.notify, "clip_posted", lambda *a, **k: None)
    orchestrator.UploadManager(orch).step()
    assert sent == [(half, "tiktok")]
    assert db.get_clip(half)["status"] == dbm.UPLOADED
    assert db.get_clip(fresh)["status"] == dbm.APPROVED


def test_on_screen_captions_hide_swears():
    from clipper import editor
    from clipper.config import Edit
    from clipper.transcribe import Word
    words = [Word(0.0, 0.3, "what"), Word(0.3, 0.5, "the"), Word(0.5, 0.9, "fuck!"), Word(1.0, 1.3, "hello")]
    ass = editor.build_ass(words, Edit(), 900)
    assert "FUCK" not in ass and "****!" in ass and "HELLO" in ass


def test_posted_clip_offers_a_clean_tiktok_caption(tmp_path, monkeypatch):
    import types
    orch, c = _app(tmp_path, monkeypatch)
    orch.uploaders = [types.SimpleNamespace(name="youtube"), types.SimpleNamespace(name="tiktok")]
    a = orch.db.add_clip(streamer="Lacy", platform="twitch", channel="lacy", status=dbm.UPLOADED,
                         title="Lacy says what the f*ck", hashtags=["#Lacy", "#GTA6", "#shorts", "#shit"])
    page = c.get(f"/clip/{a}", auth=("admin", "pw")).text
    assert "Copy TikTok caption" in page
    from clipper import clean
    assert clean.safe_title("Lacy says what the f*ck", "Lacy") + " #Lacy #GTA6 #fyp" in page
    assert "#shorts" not in page and "#shit" not in page


def test_sweary_titles_get_varied_clean_replacements():
    from clipper import clean
    titles = {clean.safe_title(f'YourRAGE in GTA 6: "f*ck number {i}"', "YourRAGE") for i in range(40)}
    assert len(titles) >= 5 and all(clean.is_clean(t) and "YourRAGE" in t for t in titles)
    assert clean.safe_title("x shit", "A") == clean.safe_title("x shit", "A")   # same clip, same title
