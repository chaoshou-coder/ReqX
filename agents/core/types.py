from __future__ import annotations

from typing import Any, Protocol


class LLMResponse(Protocol):
    content: Any


class LLMClient(Protocol):
    def invoke(self, input: Any, /, **kwargs: Any) -> LLMResponse: ...

