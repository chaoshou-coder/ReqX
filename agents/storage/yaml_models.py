from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dacite import Config, DaciteError, from_dict


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
    try:
        return from_dict(KnowledgePayloadWire, data, config=Config(strict=False))
    except DaciteError:
        return None


def parse_transcript_payload_wire(data: dict[str, Any]) -> TranscriptPayloadWire | None:
    try:
        return from_dict(TranscriptPayloadWire, data, config=Config(strict=False))
    except DaciteError:
        return None

