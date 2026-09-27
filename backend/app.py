from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from backend.config import get_settings
from backend.models import (
    CreateSessionRequest,
    HealthResponse,
    ModeRequest,
    OperatingMode,
    SelectFormRequest,
    SessionState,
    UpdateFieldRequest,
    VoiceCommandRequest,
    VoiceCommandResult,
)
from backend.services.extraction import LocalDocumentExtractor
from backend.services.forms import FormSchemaService
from backend.services.ocr import LocalOCRService, OCRResult, OCRUnavailableError
from backend.services.online import OptionalGeminiService
from backend.services.pdf import PDFGenerationService
from backend.services.validation import validate_form
from backend.services.voice import FasterWhisperService, LocalIntentParser
from backend.state import session_store

settings = get_settings()
ocr_service = LocalOCRService(settings.paddle_model_dir)
document_extractor = LocalDocumentExtractor()
form_service = FormSchemaService(settings.schema_dir)
intent_parser = LocalIntentParser()
speech_service = FasterWhisperService(settings.whisper_model_path)
pdf_service = PDFGenerationService()
online_service = OptionalGeminiService(settings.gemini_api_key, settings.gemini_model)

app = FastAPI(title="DocuVoice API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        mode="offline-first",
        paddle_model_cached=ocr_service.model_cached,
        whisper_model_cached=speech_service.model_cached,
        online_configured=online_service.available(),
        details={
            "state": "local_memory",
            "document_ai": "local_rules",
            "pdf": "PyMuPDF",
        },
    )


@app.post("/api/sessions", response_model=SessionState)
def create_session(request: CreateSessionRequest | None = None) -> SessionState:
    return session_store.create(request.mode if request else OperatingMode.OFFLINE)


@app.get("/api/sessions/{session_id}", response_model=SessionState)
def get_session(session_id: str) -> SessionState:
    return session_store.get(session_id)


@app.patch("/api/sessions/{session_id}/mode", response_model=SessionState)
def update_mode(session_id: str, request: ModeRequest) -> SessionState:
    if request.mode == OperatingMode.ONLINE and not online_service.available():
        raise HTTPException(
            status_code=409,
            detail="Online mode is not configured. Offline state was preserved.",
        )
    return session_store.mutate(
        session_id, lambda state: setattr(state, "mode", request.mode)
    )


@app.post("/api/sessions/{session_id}/documents", response_model=SessionState)
async def upload_document(
    session_id: str, file: Annotated[UploadFile, File()]
) -> SessionState:
    session_store.get(session_id)
    supported = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
    media_type = file.content_type or "application/octet-stream"
    if media_type not in supported:
        raise HTTPException(
            status_code=415, detail="Upload a PDF, PNG, JPEG, or WebP file."
        )
    content = await file.read(settings.upload_limit_mb * 1024 * 1024 + 1)
    if len(content) > settings.upload_limit_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413, detail="File exceeds the local upload limit."
        )
    try:
        ocr = ocr_service.extract(content, file.filename or "document", media_type)
        document = document_extractor.process(
            ocr, file.filename or "document", media_type
        )
    except OCRUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def add_document(state):
        state.documents.append(document)
        if state.form:
            schema = form_service.get(state.form.schema_id)
            state.form = form_service.populate(schema, state.documents)
            state.form.validation_issues = validate_form(
                schema, state.form, state.documents
            )

    return session_store.mutate(session_id, add_document)


@app.post("/api/sessions/{session_id}/demo", response_model=SessionState)
def load_synthetic_demo(session_id: str) -> SessionState:
    """Load explicitly fabricated data without touching OCR model availability."""
    session_store.get(session_id)
    aadhaar_text = """GOVERNMENT OF INDIA
Name: AARAV SHARMA
DOB: 14/08/1999
MALE
Address: 21 Lotus Residency, Andheri West, Mumbai, Maharashtra 400056
9999 9999 0019"""
    pan_text = """INCOME TAX DEPARTMENT
Name: AARAV SHARMA
Permanent Account Number
ABCPK1234F"""
    documents = [
        document_extractor.process(
            OCRResult(text=aadhaar_text, fragments=[], method="synthetic_demo"),
            "synthetic_aadhaar_demo.pdf",
            "application/pdf",
        ),
        document_extractor.process(
            OCRResult(text=pan_text, fragments=[], method="synthetic_demo"),
            "synthetic_pan_demo.pdf",
            "application/pdf",
        ),
    ]

    def add_demo(state):
        state.documents = documents
        if state.form:
            schema = form_service.get(state.form.schema_id)
            state.form = form_service.populate(schema, state.documents)
            state.form.validation_issues = validate_form(
                schema, state.form, state.documents
            )

    return session_store.mutate(session_id, add_demo)


@app.get("/api/forms")
def list_forms():
    return [form_service.as_public_summary(schema) for schema in form_service.list()]


@app.post("/api/sessions/{session_id}/form", response_model=SessionState)
def select_form(session_id: str, request: SelectFormRequest) -> SessionState:
    try:
        schema = form_service.get(request.schema_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Form schema not found") from exc

    def populate(state):
        state.form = form_service.populate(schema, state.documents)
        state.form.validation_issues = validate_form(
            schema, state.form, state.documents
        )

    return session_store.mutate(session_id, populate)


@app.patch("/api/sessions/{session_id}/fields/{field_key}", response_model=SessionState)
def update_field(
    session_id: str, field_key: str, request: UpdateFieldRequest
) -> SessionState:
    def update(state):
        if not state.form or field_key not in state.form.fields:
            raise HTTPException(status_code=404, detail="Form field not found")
        state.form.fields[field_key].value = request.value.strip()
        state.form.fields[field_key].source = "user"
        schema = form_service.get(state.form.schema_id)
        state.form.validation_issues = validate_form(
            schema, state.form, state.documents
        )

    return session_store.mutate(session_id, update)


@app.post("/api/sessions/{session_id}/validate", response_model=SessionState)
def validate_session(session_id: str) -> SessionState:
    def run_validation(state):
        if not state.form:
            raise HTTPException(
                status_code=409, detail="Select a form before validation."
            )
        schema = form_service.get(state.form.schema_id)
        state.form.validation_issues = validate_form(
            schema, state.form, state.documents
        )

    return session_store.mutate(session_id, run_validation)


def execute_voice_command(session_id: str, transcript: str) -> VoiceCommandResult:
    session = session_store.get(session_id)
    if not session.form:
        raise HTTPException(
            status_code=409, detail="Select a form before using voice commands."
        )
    schema = form_service.get(session.form.schema_id)
    parsed = intent_parser.parse(transcript, schema)
    intent = parsed["intent"]
    field = parsed.get("field")
    value = parsed.get("value")
    missing: list[str] = []
    navigate_to = None

    if intent == "update_field":
        if not field:
            message = "I could not match that field. Try using its label from the form."
        else:
            session = update_field(session_id, field, UpdateFieldRequest(value=value))
            label = next(item.label for item in schema.fields if item.key == field)
            message = f"Updated {label} to {value}."
            navigate_to = field
    elif intent == "get_missing_fields":
        missing = [
            definition.label
            for definition in schema.fields
            if definition.required
            and not session.form.fields[definition.key].value.strip()
        ]
        message = (
            "Missing fields: " + ", ".join(missing)
            if missing
            else "All required fields are complete."
        )
    elif intent == "navigate_to_field":
        if field:
            navigate_to = field
            label = next(item.label for item in schema.fields if item.key == field)
            message = f"Moving to {label}."
        else:
            message = "I could not find that section or field."
    elif intent == "validate_form":
        session = validate_session(session_id)
        count = len(session.form.validation_issues)
        message = (
            "No validation issues found."
            if count == 0
            else f"Found {count} validation issue{'s' if count != 1 else ''}."
        )
    elif intent == "generate_completed_pdf":
        message = "Your PDF is ready to preview or download."
    else:
        message = "Try “change my pincode to 400056” or “which fields are missing?”"

    return VoiceCommandResult(
        intent=intent,
        message=message,
        field=field,
        value=value,
        navigate_to=navigate_to,
        missing_fields=missing,
        session=session,
    )


@app.post("/api/sessions/{session_id}/voice/command", response_model=VoiceCommandResult)
def voice_command(session_id: str, request: VoiceCommandRequest) -> VoiceCommandResult:
    return execute_voice_command(session_id, request.transcript)


@app.post(
    "/api/sessions/{session_id}/voice/transcribe", response_model=VoiceCommandResult
)
async def transcribe_voice(
    session_id: str, file: Annotated[UploadFile, File()]
) -> VoiceCommandResult:
    suffix = Path(file.filename or "voice.webm").suffix or ".webm"
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Voice recording exceeds 10 MB.")
    try:
        with TemporaryDirectory(prefix="docuvoice-") as directory:
            path = Path(directory) / f"recording{suffix}"
            path.write_bytes(content)
            transcript = speech_service.transcribe(path)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not transcript:
        raise HTTPException(status_code=422, detail="No speech was detected.")
    return execute_voice_command(session_id, transcript)


@app.get("/api/sessions/{session_id}/export")
def export_pdf(session_id: str) -> Response:
    session = session_store.get(session_id)
    if not session.form:
        raise HTTPException(status_code=409, detail="Select a form before export.")
    schema = form_service.get(session.form.schema_id)
    content = pdf_service.generate(schema, session.form)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="docuvoice-completed-form.pdf"'
        },
    )


@app.websocket("/ws/sessions/{session_id}")
async def session_socket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    try:
        session_store.get(session_id)
        while True:
            payload = await websocket.receive_json()
            transcript = str(payload.get("transcript", "")).strip()
            if not transcript:
                await websocket.send_json({"error": "A transcript is required."})
                continue
            result = execute_voice_command(session_id, transcript)
            await websocket.send_json(result.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
    except HTTPException as exc:
        await websocket.send_json({"error": exc.detail})
        await websocket.close(code=1008)
