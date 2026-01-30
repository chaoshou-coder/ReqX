from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Literal

from .sqlite_store import BaseSqliteStore
from .yaml_store import BaseYamlStore, parse_schema_version
from .yaml_models import parse_knowledge_payload_wire


Role = Literal["user", "assistant", "system"]


@dataclass
class KnowledgeRecord:
    role: Role
    content: str
    ts: str


class KnowledgeStore(BaseYamlStore):
    def __init__(self, path: str | Path):
        super().__init__(path)
        self.project_name: str | None = None
        self.records: list[KnowledgeRecord] = []
        self.latest_spec_yaml: str | None = None

    def load(self) -> None:
        self.schema_version = 1
        self.project_name = None
        self.records = []
        self.latest_spec_yaml = None
        data = self._load_mapping()
        if not data:
            return
        payload = parse_knowledge_payload_wire(data)
        if payload is None:
            return
        self.schema_version = parse_schema_version(payload.schema_version)
        pn = payload.project_name
        self.project_name = pn.strip() if isinstance(pn, str) and pn.strip() else None
        spec = payload.latest_spec_yaml
        self.latest_spec_yaml = spec.strip() if isinstance(spec, str) and spec.strip() else None
        out: list[KnowledgeRecord] = []
        for item in payload.records:
            role = item.role
            content = item.content
            ts = item.ts
            if role not in {"user", "assistant", "system"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            if not isinstance(ts, str) or not ts.strip():
                continue
            out.append(KnowledgeRecord(role=role, content=content.strip(), ts=ts))
        self.records = out

    def save(self) -> None:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "project_name": self.project_name,
            "latest_spec_yaml": self.latest_spec_yaml,
            "records": [r.__dict__ for r in self.records],
        }
        self._atomic_save(payload)

    def reset_session(self) -> None:
        self.records = []
        self.latest_spec_yaml = None
        self.save()

    def append(self, role: Role, content: str, *, autosave: bool = True) -> None:
        text = (content or "").strip()
        if not text:
            return
        ts = datetime.now(timezone.utc).isoformat()
        self.records.append(KnowledgeRecord(role=role, content=text, ts=ts))
        if autosave:
            self.save()

    def transcript(self) -> str:
        out: list[str] = []
        for r in self.records:
            name = "用户" if r.role == "user" else ("助手" if r.role == "assistant" else "系统")
            out.append(f"{name}: {r.content}")
        return "\n".join(out).strip()


class SqliteKnowledgeStore(BaseSqliteStore):
    def __init__(self, path: str | Path):
        super().__init__(path)
        self.schema_version = 1
        self.project_name: str | None = None
        self.records: list[KnowledgeRecord] = []
        self.latest_spec_yaml: str | None = None
        self._persisted_count = 0

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT NOT NULL)"
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
        pn = (meta.get("project_name") or "").strip()
        self.project_name = pn or None
        spec = (meta.get("latest_spec_yaml") or "").strip()
        self.latest_spec_yaml = spec or None
        rows = con.execute("SELECT role, content, ts FROM records ORDER BY id ASC").fetchall()
        out: list[KnowledgeRecord] = []
        for role, content, ts in rows:
            if role not in {"user", "assistant", "system"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            if not isinstance(ts, str) or not ts.strip():
                continue
            out.append(KnowledgeRecord(role=role, content=content.strip(), ts=ts))
        self.records = out
        self._persisted_count = len(self.records)

    def save(self) -> None:
        with self._transaction() as con:
            con.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("schema_version", str(int(self.schema_version or 1))),
            )
            con.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("project_name", self.project_name or ""),
            )
            con.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("latest_spec_yaml", self.latest_spec_yaml or ""),
            )

            if len(self.records) < self._persisted_count:
                con.execute("DELETE FROM records")
                for r in self.records:
                    con.execute(
                        "INSERT INTO records(role, content, ts) VALUES(?, ?, ?)",
                        (r.role, r.content, r.ts),
                    )
            else:
                for r in self.records[self._persisted_count :]:
                    con.execute(
                        "INSERT INTO records(role, content, ts) VALUES(?, ?, ?)",
                        (r.role, r.content, r.ts),
                    )
        self._persisted_count = len(self.records)

    def reset_session(self) -> None:
        self.records = []
        self.latest_spec_yaml = None
        with self._transaction() as con:
            con.execute("DELETE FROM records")
            con.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("latest_spec_yaml", ""),
            )
        self._persisted_count = 0

    def append(self, role: Role, content: str, *, autosave: bool = True) -> None:
        text = (content or "").strip()
        if not text:
            return
        ts = datetime.now(timezone.utc).isoformat()
        self.records.append(KnowledgeRecord(role=role, content=text, ts=ts))
        if autosave:
            with self._transaction() as con:
                con.execute(
                    "INSERT INTO records(role, content, ts) VALUES(?, ?, ?)",
                    (role, text, ts),
                )
            self._persisted_count = len(self.records)

    def transcript(self) -> str:
        out: list[str] = []
        for r in self.records:
            name = "用户" if r.role == "user" else ("助手" if r.role == "assistant" else "系统")
            out.append(f"{name}: {r.content}")
        return "\n".join(out).strip()


def open_knowledge_store(path: str | Path) -> KnowledgeStore | SqliteKnowledgeStore:
    p = Path(path)
    if p.suffix.lower() == ".db":
        return SqliteKnowledgeStore(p)
    return KnowledgeStore(p)
