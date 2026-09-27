from __future__ import annotations

from typing import Protocol


class OnlineAIService(Protocol):
    def available(self) -> bool: ...

    def enrich_document(self, raw_text: str, local_fields: dict) -> dict: ...


class OptionalGeminiService:
    """Optional enrichment boundary; callers must retain local state on failure."""

    def __init__(self, api_key: str | None, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def available(self) -> bool:
        return bool(self.api_key)

    def enrich_document(self, raw_text: str, local_fields: dict) -> dict:
        if not self.api_key:
            return local_fields
        # Online enrichment is intentionally opt-in and is not called by the
        # offline pipeline. A later phase can implement structured generation
        # here without changing route, state, OCR, or form-mapping code.
        return local_fields
