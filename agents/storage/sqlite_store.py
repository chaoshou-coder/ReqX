from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


class BaseSqliteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._con: sqlite3.Connection | None = None

    def close(self) -> None:
        con = self._con
        self._con = None
        if con is not None:
            try:
                con.close()
            except Exception:
                pass

    def _connect(self) -> sqlite3.Connection:
        if self._con is not None:
            return self._con
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path), timeout=10)
        try:
            con.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        try:
            con.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        try:
            con.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        try:
            con.execute("PRAGMA busy_timeout=10000")
        except Exception:
            pass
        self._con = con
        return con

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
        raise NotImplementedError

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        self._ensure_schema(con)
        with con:
            yield con

