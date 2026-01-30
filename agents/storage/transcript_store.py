from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Literal

from .sqlite_store import BaseSqliteStore
from .yaml_store import BaseYamlStore, parse_schema_version
from .yaml_models import parse_transcript_payload_wire


Role = Literal["user", "assistant", "system"]


@dataclass
class TranscriptTurn:
    role: Role
    content: str
    ts: str


class TranscriptStore(BaseYamlStore):
    def __init__(self, path: str | Path):
        super().__init__(path)
        self.turns: list[TranscriptTurn] = []

    def clear(self, *, autosave: bool = True) -> None:
        self.turns.clear()
        if autosave:
            self.save()

    def load(self) -> None:
        self.schema_version = 1
        self.turns = []
        data = self._load_mapping()
        if not data:
            return
        payload = parse_transcript_payload_wire(data)
        if payload is None:
            return
        self.schema_version = parse_schema_version(payload.schema_version)
        out: list[TranscriptTurn] = []
        for item in payload.turns:
            role = item.role
            content = item.content
            ts = item.ts
            if role not in {"user", "assistant", "system"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            if not isinstance(ts, str) or not ts.strip():
                continue
            out.append(TranscriptTurn(role=role, content=content.strip(), ts=ts))
        self.turns = out

    def save(self) -> None:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "turns": [t.__dict__ for t in self.turns],
        }
        self._atomic_save(payload)

    def append(self, role: Role, content: str, *, autosave: bool = True) -> None:
        text = (content or "").strip()
        if not text:
            return
        ts = datetime.now(timezone.utc).isoformat()
        self.turns.append(TranscriptTurn(role=role, content=text, ts=ts))
        if autosave:
            self.save()

    def transcript_text(self) -> str:
        out: list[str] = []
        for t in self.turns:
            name = "用户" if t.role == "user" else ("助手" if t.role == "assistant" else "系统")
            out.append(f"{name}: {t.content}")
        return "\n".join(out).strip()


class SqliteTranscriptStore(BaseSqliteStore):
    def __init__(self, path: str | Path):
        super().__init__(path)
        self.schema_version = 1
        self.turns: list[TranscriptTurn] = []
        self._persisted_count = 0

    def clear(self, *, autosave: bool = True) -> None:
        self.turns.clear()
        self._persisted_count = 0
        if autosave:
            with self._transaction() as con:
                con.execute("DELETE FROM turns")

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT NOT NULL)"
        )

    def load(self) -> None:
        if not self.path.exists():
            return
        con = self._connect()
        self._ensure_schema(con)
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        try:
            self.schema_version = int(meta.get("schema_version", "1") or "1")
        except Exception:
            self.schema_version = 1
        rows = con.execute("SELECT role, content, ts FROM turns ORDER BY id ASC").fetchall()
        out: list[TranscriptTurn] = []
        for role, content, ts in rows:
            if role not in {"user", "assistant", "system"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            if not isinstance(ts, str) or not ts.strip():
                continue
            out.append(TranscriptTurn(role=role, content=content.strip(), ts=ts))
        self.turns = out
        self._persisted_count = len(self.turns)

    def save(self) -> None:
        with self._transaction() as con:
            con.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("schema_version", str(int(self.schema_version or 1))),
            )
            if len(self.turns) < self._persisted_count:
                con.execute("DELETE FROM turns")
                for t in self.turns:
                    con.execute(
                        "INSERT INTO turns(role, content, ts) VALUES(?, ?, ?)",
                        (t.role, t.content, t.ts),
                    )
            else:
                for t in self.turns[self._persisted_count :]:
                    con.execute(
                        "INSERT INTO turns(role, content, ts) VALUES(?, ?, ?)",
                        (t.role, t.content, t.ts),
                    )
        self._persisted_count = len(self.turns)

    def append(self, role: Role, content: str, *, autosave: bool = True) -> None:
        text = (content or "").strip()
        if not text:
            return
        ts = datetime.now(timezone.utc).isoformat()
        self.turns.append(TranscriptTurn(role=role, content=text, ts=ts))
        if autosave:
            with self._transaction() as con:
                con.execute(
                    "INSERT INTO turns(role, content, ts) VALUES(?, ?, ?)",
                    (role, text, ts),
                )
            self._persisted_count = len(self.turns)

    def transcript_text(self) -> str:
        out: list[str] = []
        for t in self.turns:
            name = "用户" if t.role == "user" else ("助手" if t.role == "assistant" else "系统")
            out.append(f"{name}: {t.content}")
        return "\n".join(out).strip()


def open_transcript_store(path: str | Path) -> TranscriptStore | SqliteTranscriptStore:
    p = Path(path)
    if p.suffix.lower() == ".db":
        return SqliteTranscriptStore(p)
    return TranscriptStore(p)
