from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any
import uuid

import yaml


def parse_schema_version(value: Any) -> int:
    if isinstance(value, int):
        return value if value > 0 else 1
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 1
        try:
            v = int(s)
            return v if v > 0 else 1
        except Exception:
            return 1
    return 1


class BaseYamlStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.schema_version = 1

    def _load_mapping(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        raw = self.path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw) or {}
        except Exception:
            self._backup_broken_file()
            self.schema_version = 1
            return None
        if not isinstance(data, dict):
            return None
        return data

    def _backup_broken_file(self) -> None:
        backup = self.path.with_name(
            f"{self.path.name}.broken.{int(datetime.now(timezone.utc).timestamp())}.{uuid.uuid4().hex[:8]}.bak"
        )
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            self.path.replace(backup)
        except Exception:
            return

    def _atomic_save(self, payload: dict[str, Any]) -> None:
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
