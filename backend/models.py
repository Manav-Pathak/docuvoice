from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class OperatingMode(StrEnum):
    OFFLINE = "offline"
    ONLINE = "online"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class OCRFragment(BaseModel):
    text: str
    confidence: float | None = None
    page: int = 1
    bbox: list[float] | None = None


class FieldEvidence(BaseModel):
    document_id: str
    document_name: str
    raw_value: str
    extraction_method: str = "local_rules"
    ocr_score: float | None = None


class ExtractedField(BaseModel):
    key: str
    value: str
    normalized_value: str
    evidence: list[FieldEvidence] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)


class ProcessedDocument(BaseModel):
    id: str
    filename: str
    media_type: str
    document_type: str
    extraction_method: str
    raw_text: str
    fragments: list[OCRFragment] = Field(default_factory=list)
    fields: dict[str, ExtractedField] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class FormFieldDefinition(BaseModel):
    key: str
    label: str
    section: str
    field_type: str = "text"
    required: bool = False
    aliases: list[str] = Field(default_factory=list)
    pdf_field_name: str | None = None
    coordinates: dict[str, float] | None = None


class FormSchema(BaseModel):
    id: str
    title: str
    description: str
    version: int = 1
    fields: list[FormFieldDefinition]


class FormValue(BaseModel):
    value: str = ""
    source: str = "empty"
    source_document_id: str | None = None
    source_document_name: str | None = None
    confidence: float | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: Severity
    field: str | None = None
    related_documents: list[str] = Field(default_factory=list)


class FormState(BaseModel):
    schema_id: str
    title: str
    fields: dict[str, FormValue]
    validation_issues: list[ValidationIssue] = Field(default_factory=list)


class SessionState(BaseModel):
    id: str
    mode: OperatingMode = OperatingMode.OFFLINE
    documents: list[ProcessedDocument] = Field(default_factory=list)
    form: FormState | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CreateSessionRequest(BaseModel):
    mode: OperatingMode = OperatingMode.OFFLINE


class ModeRequest(BaseModel):
    mode: OperatingMode


class SelectFormRequest(BaseModel):
    schema_id: str


class UpdateFieldRequest(BaseModel):
    value: str


class VoiceCommandRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=500)


class VoiceCommandResult(BaseModel):
    intent: str
    message: str
    field: str | None = None
    value: str | None = None
    navigate_to: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    session: SessionState


class HealthResponse(BaseModel):
    status: str
    mode: str
    paddle_model_cached: bool
    whisper_model_cached: bool
    online_configured: bool
    details: dict[str, Any] = Field(default_factory=dict)
