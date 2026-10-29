"""Local SQLite store for app state: sessions, consent rules and the action trail.

The trail is append-only: it is both the audit log and the raw material the
reflection loop (F3) reads. LangGraph checkpoints live in the same database file,
in their own tables, through a separate connection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    model_profile TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consent_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    resource_pattern TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session_id TEXT,
    kind TEXT NOT NULL,
    tool_call_id TEXT,
    tool_name TEXT,
    args TEXT,
    decision TEXT,
    rule_id INTEGER,
    ok INTEGER,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS trail_session ON trail (session_id, id);
"""


@dataclass(frozen=True)
class Rule:
    id: int
    tool_name: str
    pattern: str
    created_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @classmethod
    async def open(cls, path: Path) -> Store:
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.executescript(SCHEMA)
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self._conn.close()

    # Sessions

    async def create_session(self, session_id: str, model_profile: str) -> None:
        await self._conn.execute(
            "INSERT INTO sessions (id, model_profile, created_at) VALUES (?, ?, ?)",
            (session_id, model_profile, _now()),
        )
        await self._conn.commit()

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        async with self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_session(self, session_id: str) -> None:
        await self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await self._conn.commit()

    # Consent rules (the allowlist)

    async def add_rule(self, tool_name: str, pattern: str) -> int:
        cur = await self._conn.execute(
            "INSERT INTO consent_rules (tool_name, resource_pattern, created_at) VALUES (?, ?, ?)",
            (tool_name, pattern, _now()),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def active_rules(self, tool_name: str | None = None) -> list[Rule]:
        query = "SELECT id, tool_name, resource_pattern, created_at FROM consent_rules WHERE revoked_at IS NULL"
        params: tuple[Any, ...] = ()
        if tool_name is not None:
            query += " AND tool_name = ?"
            params = (tool_name,)
        async with self._conn.execute(query + " ORDER BY id", params) as cur:
            rows = await cur.fetchall()
        return [Rule(r["id"], r["tool_name"], r["resource_pattern"], r["created_at"]) for r in rows]

    async def revoke_rule(self, rule_id: int) -> bool:
        cur = await self._conn.execute(
            "UPDATE consent_rules SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (_now(), rule_id),
        )
        await self._conn.commit()
        return cur.rowcount > 0

    # Trail

    async def record(
        self,
        kind: str,
        *,
        session_id: str | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        args: dict[str, Any] | None = None,
        decision: str | None = None,
        rule_id: int | None = None,
        ok: bool | None = None,
        detail: str | None = None,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO trail (ts, session_id, kind, tool_call_id, tool_name, args, decision, rule_id, ok, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now(),
                session_id,
                kind,
                tool_call_id,
                tool_name,
                json.dumps(args, ensure_ascii=False) if args is not None else None,
                decision,
                rule_id,
                None if ok is None else int(ok),
                detail,
            ),
        )
        await self._conn.commit()

    async def trail(self, session_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM trail", ()
        if session_id is not None:
            query, params = query + " WHERE session_id = ?", (session_id,)
        async with self._conn.execute(query + " ORDER BY id DESC LIMIT ?", (*params, limit)) as cur:
            rows = await cur.fetchall()
        entries = []
        for row in rows:
            entry = dict(row)
            entry["args"] = json.loads(entry["args"]) if entry["args"] else None
            entry["ok"] = None if entry["ok"] is None else bool(entry["ok"])
            entries.append(entry)
        return entries
