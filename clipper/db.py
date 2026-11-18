"""Tiny SQLite store for clips and discovered streams. Thread-safe via a lock."""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    streamer TEXT NOT NULL,
    platform TEXT NOT NULL,
    channel TEXT NOT NULL,
    stream_url TEXT,
    stream_title TEXT,
    created_at REAL NOT NULL,
    moment_at REAL,
    score REAL,
    signals TEXT,
    status TEXT NOT NULL,
    raw_path TEXT,
    final_path TEXT,
    transcript TEXT,
    title TEXT,
    description TEXT,
    hashtags TEXT,
    llm_score REAL,
    llm_reason TEXT,
    uploads TEXT DEFAULT '{}',
    error TEXT,
    public_token TEXT
);
CREATE INDEX IF NOT EXISTS clips_status ON clips(status);
CREATE TABLE IF NOT EXISTS discovered (
    platform TEXT NOT NULL,
    login TEXT NOT NULL,
    display_name TEXT,
    viewers INTEGER,
    title TEXT,
    language TEXT,
    last_seen REAL,
    PRIMARY KEY (platform, login)
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

# Clip lifecycle
PROCESSING = "processing"          # cutting / editing / transcribing
READY = "ready"                    # waiting for approval (review mode)
APPROVED = "approved"              # queued for upload
UPLOADING = "uploading"
UPLOADED = "uploaded"
REJECTED = "rejected"              # rejected by a human
LOW_SCORE = "low_score"            # LLM said it's not good enough
NEEDS_PERMISSION = "needs_permission"  # streamer has not granted permission
FAILED = "failed"

JSON_FIELDS = {"signals", "hashtags", "uploads"}


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # --- clips --------------------------------------------------------------------
    def add_clip(self, **fields: Any) -> int:
        fields.setdefault("created_at", time.time())
        fields.setdefault("status", PROCESSING)
        fields.setdefault("public_token", secrets.token_urlsafe(16))
        fields = {k: (json.dumps(v) if k in JSON_FIELDS else v) for k, v in fields.items()}
        cols = ", ".join(fields)
        qs = ", ".join("?" for _ in fields)
        with self._lock:
            cur = self._conn.execute(f"INSERT INTO clips ({cols}) VALUES ({qs})", list(fields.values()))
            self._conn.commit()
            return int(cur.lastrowid)

    def update_clip(self, clip_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields = {k: (json.dumps(v) if k in JSON_FIELDS else v) for k, v in fields.items()}
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE clips SET {sets} WHERE id = ?", [*fields.values(), clip_id])
            self._conn.commit()

    def _row(self, r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in JSON_FIELDS:
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except (TypeError, ValueError):
                    pass
        return d

    def get_clip(self, clip_id: int) -> dict | None:
        with self._lock:
            return self._row(self._conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone())

    def get_clip_by_token(self, token: str) -> dict | None:
        with self._lock:
            return self._row(self._conn.execute("SELECT * FROM clips WHERE public_token = ?", (token,)).fetchone())

    def list_clips(self, statuses: list[str] | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            if statuses:
                q = f"SELECT * FROM clips WHERE status IN ({','.join('?' for _ in statuses)}) ORDER BY id DESC LIMIT ?"
                rows = self._conn.execute(q, [*statuses, limit]).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM clips ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [self._row(r) for r in rows]

    def next_approved(self) -> dict | None:
        with self._lock:
            return self._row(self._conn.execute(
                "SELECT * FROM clips WHERE status = ? ORDER BY COALESCE(llm_score, 0) DESC, id ASC LIMIT 1",
                (APPROVED,)).fetchone())

    def uploads_since(self, since_ts: float) -> int:
        """Number of clips uploaded (to at least one target) since a timestamp."""
        with self._lock:
            rows = self._conn.execute("SELECT uploads FROM clips WHERE status IN (?, ?)", (UPLOADED, UPLOADING)).fetchall()
        n = 0
        for r in rows:
            try:
                ups = json.loads(r["uploads"] or "{}")
            except ValueError:
                continue
            if any((u or {}).get("at", 0) >= since_ts for u in ups.values() if isinstance(u, dict)):
                n += 1
        return n

    def last_upload_at(self) -> float:
        with self._lock:
            rows = self._conn.execute("SELECT uploads FROM clips WHERE status = ?", (UPLOADED,)).fetchall()
        last = 0.0
        for r in rows:
            try:
                ups = json.loads(r["uploads"] or "{}")
            except ValueError:
                continue
            for u in ups.values():
                if isinstance(u, dict):
                    last = max(last, float(u.get("at", 0)))
        return last

    # --- discovery ----------------------------------------------------------------
    def upsert_discovered(self, platform: str, login: str, display_name: str, viewers: int, title: str, language: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO discovered (platform, login, display_name, viewers, title, language, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, login) DO UPDATE SET "
                "display_name=excluded.display_name, viewers=excluded.viewers, title=excluded.title, "
                "language=excluded.language, last_seen=excluded.last_seen",
                (platform, login, display_name, viewers, title, language, time.time()))
            self._conn.commit()

    def list_discovered(self, max_age_hours: float = 24) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM discovered WHERE last_seen > ? ORDER BY viewers DESC LIMIT 100",
                (time.time() - max_age_hours * 3600,)).fetchall()
            return [dict(r) for r in rows]

    # --- key/value (tokens etc.) --------------------------------------------------
    def kv_get(self, k: str) -> str | None:
        with self._lock:
            r = self._conn.execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
            return r["v"] if r else None

    def kv_set(self, k: str, v: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            self._conn.commit()
