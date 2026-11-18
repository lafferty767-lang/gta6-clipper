import time

from clipper import db as dbm
from clipper.config import Edit
from clipper.editor import build_ass, chunk_words
from clipper.transcribe import Word


def test_chunks_and_ass():
    words = [Word(0.0, 0.3, "oh"), Word(0.35, 0.6, "my"), Word(0.65, 0.9, "god"), Word(0.95, 1.2, "bro"),
             Word(3.0, 3.3, "what")]
    chunks = chunk_words(words)
    assert [len(c) for c in chunks] == [3, 1, 1]
    ass = build_ass(words, Edit(), 1200)
    assert ass.count("Dialogue:") == 5
    assert "{\\c&H00FFFF&}MY{\\c&HFFFFFF&}" in ass


def test_db_roundtrip(tmp_path):
    db = dbm.DB(tmp_path / "t.db")
    cid = db.add_clip(streamer="CaseOh", platform="twitch", channel="caseoh_", signals={"score": 7})
    db.update_clip(cid, status=dbm.APPROVED, hashtags=["#GTA6"], uploads={})
    c = db.get_clip(cid)
    assert c["signals"] == {"score": 7} and c["hashtags"] == ["#GTA6"]
    assert db.next_approved()["id"] == cid
    db.update_clip(cid, status=dbm.UPLOADED, uploads={"youtube": {"ok": True, "at": time.time()}})
    assert db.uploads_since(time.time() - 60) == 1
    assert db.get_clip_by_token(c["public_token"])["id"] == cid
