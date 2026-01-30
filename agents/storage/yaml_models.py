from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from dacite import Config as _DaciteConfig
    from dacite import DaciteError as _DaciteError
    from dacite import from_dict as _from_dict
except Exception:
    _DaciteConfig = None
    _DaciteError = Exception
    _from_dict = None


@dataclass
class KnowledgeRecordWire:
    role: str | None = None
    content: str | None = None
    ts: str | None = None


@dataclass
class KnowledgePayloadWire:
    schema_version: int | str | None = 1
    project_name: str | None = None
    latest_spec_yaml: str | None = None
    records: list[KnowledgeRecordWire] = field(default_factory=list)


@dataclass
class TranscriptTurnWire:
    role: str | None = None
    content: str | None = None
    ts: str | None = None


@dataclass
class TranscriptPayloadWire:
    schema_version: int | str | None = 1
    turns: list[TranscriptTurnWire] = field(default_factory=list)


def parse_knowledge_payload_wire(data: dict[str, Any]) -> KnowledgePayloadWire | None:
    if not isinstance(data, dict):
        return None
    if _from_dict is not None and _DaciteConfig is not None:
        try:
            return _from_dict(KnowledgePayloadWire, data, config=_DaciteConfig(strict=False))
        except _DaciteError:
            return None

    records_raw = data.get("records") or []
    records: list[KnowledgeRecordWire] = []
    if isinstance(records_raw, list):
        for item in records_raw:
            if not isinstance(item, dict):
                continue
            records.append(
                KnowledgeRecordWire(
                    role=item.get("role") if isinstance(item.get("role"), str) else None,
                    content=item.get("content") if isinstance(item.get("content"), str) else None,
                    ts=item.get("ts") if isinstance(item.get("ts"), str) else None,
                )
            )
    return KnowledgePayloadWire(
        schema_version=data.get("schema_version", 1),
        project_name=data.get("project_name") if isinstance(data.get("project_name"), str) else None,
        latest_spec_yaml=data.get("latest_spec_yaml") if isinstance(data.get("latest_spec_yaml"), str) else None,
        records=records,
    )


def parse_transcript_payload_wire(data: dict[str, Any]) -> TranscriptPayloadWire | None:
    if not isinstance(data, dict):
        return None
    if _from_dict is not None and _DaciteConfig is not None:
        try:
            return _from_dict(TranscriptPayloadWire, data, config=_DaciteConfig(strict=False))
        except _DaciteError:
            return None

    turns_raw = data.get("turns") or []
    turns: list[TranscriptTurnWire] = []
    if isinstance(turns_raw, list):
        for item in turns_raw:
            if not isinstance(item, dict):
                continue
            turns.append(
                TranscriptTurnWire(
                    role=item.get("role") if isinstance(item.get("role"), str) else None,
                    content=item.get("content") if isinstance(item.get("content"), str) else None,
                    ts=item.get("ts") if isinstance(item.get("ts"), str) else None,
                )
            )
    return TranscriptPayloadWire(schema_version=data.get("schema_version", 1), turns=turns)
