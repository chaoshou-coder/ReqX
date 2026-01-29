from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Literal
import uuid

import yaml


Role = Literal["user", "assistant", "system"]


@dataclass
class TranscriptTurn:
    role: Role
    content: str
    ts: str


class TranscriptStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.schema_version = 1
        self.turns: list[TranscriptTurn] = []

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = self.path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw) or {}
        except Exception:
            backup = self.path.with_name(f"{self.path.name}.broken.{int(datetime.now(timezone.utc).timestamp())}.{uuid.uuid4().hex[:8]}.bak")
            try:
                backup.parent.mkdir(parents=True, exist_ok=True)
                self.path.replace(backup)
            except Exception:
                pass
            self.schema_version = 1
            self.turns = []
            return
        if not isinstance(data, dict):
            return
        self.schema_version = int(data.get("schema_version", 1) or 1)
        items = data.get("turns", [])
        if not isinstance(items, list):
            return
        out: list[TranscriptTurn] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            ts = item.get("ts")
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

    def append(self, role: Role, content: str) -> None:
        text = (content or "").strip()
        if not text:
            return
        ts = datetime.now(timezone.utc).isoformat()
        self.turns.append(TranscriptTurn(role=role, content=text, ts=ts))
        self.save()

    def transcript_text(self) -> str:
        out: list[str] = []
        for t in self.turns:
            name = "用户" if t.role == "user" else ("助手" if t.role == "assistant" else "系统")
            out.append(f"{name}: {t.content}")
        return "\n".join(out).strip()
