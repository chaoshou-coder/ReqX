from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..storage.knowledge_store import Role, open_knowledge_store


def _resolve_path(value: str | Path, *, base_dir: Path | None = None) -> Path:
    p = Path(value)
    if not p.is_absolute() and base_dir is not None:
        p = base_dir / p
    resolved = p.expanduser().resolve()
    if base_dir is not None:
        base = base_dir.expanduser().resolve()
        try:
            resolved.relative_to(base)
        except Exception as e:
            raise ValueError("knowledge_path_outside_base_dir") from e
    return resolved


@dataclass(frozen=True)
class KnowledgeSnapshot:
    schema_version: int
    project_name: str | None
    latest_spec_yaml: str | None
    records: list[dict[str, Any]]


class KnowledgeService:
    def __init__(self, *, base_dir: str | Path | None = None, default_path: str | Path | None = None):
        self._base_dir = Path(base_dir).expanduser().resolve() if base_dir is not None else None
        self._default_path = Path(default_path).expanduser() if default_path is not None else None

    def resolve_path(self, knowledge_path: str | Path | None) -> Path:
        if knowledge_path is None:
            if self._default_path is None:
                raise ValueError("knowledge_path_required")
            return _resolve_path(self._default_path, base_dir=self._base_dir)
        return _resolve_path(knowledge_path, base_dir=self._base_dir)

    def read(self, knowledge_path: str | Path | None = None) -> KnowledgeSnapshot:
        path = self.resolve_path(knowledge_path)
        store = open_knowledge_store(path)
        store.load()
        return KnowledgeSnapshot(
            schema_version=store.schema_version,
            project_name=store.project_name,
            latest_spec_yaml=store.latest_spec_yaml,
            records=[r.__dict__ for r in store.records],
        )

    def append_items(
        self,
        items: Iterable[str],
        *,
        knowledge_path: str | Path | None = None,
        role: Role = "system",
        dry_run: bool = False,
    ) -> int:
        path = self.resolve_path(knowledge_path)
        store = open_knowledge_store(path)
        store.load()
        n = 0
        for item in items:
            text = (item or "").strip()
            if not text:
                continue
            store.append(role, text, autosave=False)
            n += 1
        if n and (not dry_run):
            store.save()
        return n

    def set_project_name(
        self, project_name: str | None, *, knowledge_path: str | Path | None = None, dry_run: bool = False
    ) -> None:
        path = self.resolve_path(knowledge_path)
        store = open_knowledge_store(path)
        store.load()
        name = (project_name or "").strip()
        store.project_name = name or None
        if not dry_run:
            store.save()

    def set_latest_spec_yaml(
        self, latest_spec_yaml: str | None, *, knowledge_path: str | Path | None = None, dry_run: bool = False
    ) -> None:
        path = self.resolve_path(knowledge_path)
        store = open_knowledge_store(path)
        store.load()
        text = (latest_spec_yaml or "").strip()
        store.latest_spec_yaml = text or None
        if not dry_run:
            store.save()

