"""Explicit memory stores: completed turns, scoped facts, run state and artifacts.

Nothing here infers or silently saves a long-term fact. Callers choose when to
remember, which scope to search, and which completed turns become history.
SQLite files are private local state, not immutable or encrypted audit storage.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Any
import uuid


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _label(value: str, name: str, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{name} must be nonempty text of at most {maximum} characters')
    return value


def _utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class MemoryStore:
    """A durable SQLite store with explicit scope, provenance and deletion."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    question TEXT NOT NULL, answer TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id, id);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, key TEXT NOT NULL,
                    text TEXT NOT NULL, source TEXT NOT NULL, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL, expires_at REAL,
                    UNIQUE(scope, key));
                CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope, updated_at);
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, content TEXT NOT NULL,
                    metadata TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    event TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(run_id, sequence));
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def add_turn(self, session_id: str, question: str, answer: str) -> None:
        """Persist one completed user/assistant pair, not an in-flight tool trace."""
        _label(session_id, 'session_id')
        if not isinstance(question, str) or not isinstance(answer, str):
            raise ValueError('Completed turns require text question and answer')
        with self._connect() as con:
            con.execute('INSERT INTO turns(session_id, question, answer, created_at) VALUES(?,?,?,?)',
                        (session_id, question, answer, time.time()))

    def history(self, session_id: str, limit: int = 4) -> list[dict]:
        _label(session_id, 'session_id')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 100:
            raise ValueError('history limit must be between 0 and 100')
        with self._connect() as con:
            rows = con.execute('SELECT question,answer FROM turns WHERE session_id=? ORDER BY id DESC LIMIT ?',
                               (session_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def remember(self, scope: str, key: str, text: str, source: str,
                 ttl_seconds: int | float | None = None) -> dict:
        _label(scope, 'scope'); _label(key, 'key')
        _label(text, 'text', 20000); _label(source, 'source', 2000)
        if ttl_seconds is not None:
            try:
                valid_ttl = not isinstance(ttl_seconds, bool) and isinstance(ttl_seconds, (int, float)) and math.isfinite(ttl_seconds) and ttl_seconds > 0
            except OverflowError:
                valid_ttl = False
            if not valid_ttl:
                raise ValueError('ttl_seconds must be a positive finite duration or None')
        now = time.time()
        expires = now + ttl_seconds if ttl_seconds is not None else None
        if expires is not None:
            try:
                _utc(expires)
            except (ValueError, OverflowError, OSError):
                raise ValueError('Expiry is outside the supported UTC timestamp range') from None
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            old = con.execute('SELECT id FROM memories WHERE scope=? AND key=?', (scope, key)).fetchone()
            identifier = old['id'] if old else 'memory_' + uuid.uuid4().hex
            con.execute('''INSERT INTO memories(id,scope,key,text,source,created_at,updated_at,expires_at)
                           VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET
                           text=excluded.text, source=excluded.source,
                           updated_at=excluded.updated_at, expires_at=excluded.expires_at''',
                        (identifier, scope, key, text, source, now, now, expires))
            row = con.execute('SELECT * FROM memories WHERE id=?', (identifier,)).fetchone()
        return self._memory_record(row)

    @staticmethod
    def _memory_record(row) -> dict:
        result = dict(row)
        for name in ('created_at', 'updated_at', 'expires_at'):
            if result[name] is not None:
                result[name] = _utc(result[name])
        return result

    def search(self, scope: str, query: str, limit: int = 5) -> list[dict]:
        """Rank scoped, unexpired records by literal token overlap.

        This deliberately simple lexical baseline is not vector/semantic memory.
        Scope and expiry are applied before text is examined; reading does not
        delete expired records or add implicit facts.
        """
        _label(scope, 'scope')
        if not isinstance(query, str) or len(query) > 5000:
            raise ValueError('query must be text of at most 5000 characters')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError('memory search limit must be between 1 and 50')
        tokens = set(re.findall(r'\w+', query.casefold()))
        if not tokens:
            return []
        with self._connect() as con:
            rows = con.execute('''SELECT * FROM memories WHERE scope=?
                                  AND (expires_at IS NULL OR expires_at>?)
                                  ORDER BY updated_at DESC, id''', (scope, time.time())).fetchall()
        ranked = []
        for row in rows:
            keys = set(re.findall(r'\w+', row['key'].casefold()))
            words = set(re.findall(r'\w+', row['text'].casefold()))
            score = 3 * len(tokens & keys) + len(tokens & words)
            if score:
                ranked.append((score, row['updated_at'], row['id'], row))
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return [self._memory_record(row) for _, _, _, row in ranked[:limit]]

    def forget(self, scope: str, key: str) -> bool:
        _label(scope, 'scope'); _label(key, 'key')
        with self._connect() as con:
            cursor = con.execute('DELETE FROM memories WHERE scope=? AND key=?', (scope, key))
        return bool(cursor.rowcount)

    def put_artifact(self, run_id: str, content: Any, metadata: dict | None = None) -> str:
        _label(run_id, 'run_id')
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError('metadata must be an object')
        encoded, encoded_metadata = _json(content), _json(metadata or {})
        digest = hashlib.sha256(_json([run_id, content, metadata or {}]).encode()).hexdigest()
        identifier = 'artifact_' + digest
        with self._connect() as con:
            con.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?)',
                        (identifier, run_id, encoded, encoded_metadata, time.time()))
        return identifier

    def get_artifact(self, identifier: str) -> dict:
        _label(identifier, 'artifact_id')
        with self._connect() as con:
            row = con.execute('SELECT * FROM artifacts WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise KeyError('Unknown artifact')
        return {'id': row['id'], 'run_id': row['run_id'], 'content': json.loads(row['content']),
                'metadata': json.loads(row['metadata']), 'created_at': _utc(row['created_at'])}

    def save_run(self, run_id: str, payload: dict) -> None:
        _label(run_id, 'run_id')
        if not isinstance(payload, dict):
            raise ValueError('Run payload must be an object')
        encoded, now = _json(payload), time.time()
        with self._connect() as con:
            con.execute('''INSERT INTO runs VALUES(?,?,?,?) ON CONFLICT(run_id)
                           DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at''',
                        (run_id, encoded, now, now))

    def load_run(self, run_id: str) -> dict:
        _label(run_id, 'run_id')
        with self._connect() as con:
            row = con.execute('SELECT payload FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise KeyError('Unknown run')
        return json.loads(row['payload'])

    def append_event(self, run_id: str, event: dict) -> int:
        _label(run_id, 'run_id')
        if not isinstance(event, dict):
            raise ValueError('Event must be an object')
        encoded = _json(event)
        with self._connect() as con:
            # Reserve and append within one write transaction across threads.
            con.execute('BEGIN IMMEDIATE')
            sequence = con.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE run_id=?',
                                   (run_id,)).fetchone()[0]
            con.execute('INSERT INTO events VALUES(?,?,?,?)', (run_id, sequence, encoded, time.time()))
        return sequence

    def events(self, run_id: str) -> list[dict]:
        _label(run_id, 'run_id')
        with self._connect() as con:
            rows = con.execute('SELECT sequence,event,created_at FROM events WHERE run_id=? ORDER BY sequence',
                               (run_id,)).fetchall()
        return [{'sequence': row['sequence'], 'event': json.loads(row['event']),
                 'created_at': _utc(row['created_at'])} for row in rows]
