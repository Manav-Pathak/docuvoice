from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

from fastapi import HTTPException

from backend.models import (
    FormSchema,
    FormState,
    OperatingMode,
    SessionState,
    UploadedFormTemplate,
    utc_now,
)


@dataclass(frozen=True)
class StoredFormTemplate:
    filename: str
    content: bytes
    schema: FormSchema


class SessionStore:
    """Thread-safe application-owned state for the local demo.

    The interface deliberately hides storage details so a durable local store can
    replace this in-memory implementation without touching routes or services.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._form_templates: dict[str, StoredFormTemplate] = {}
        self._lock = RLock()

    def create(self, mode: OperatingMode = OperatingMode.OFFLINE) -> SessionState:
        with self._lock:
            session = SessionState(id=str(uuid4()), mode=mode)
            self._sessions[session.id] = session
            return session.model_copy(deep=True)

    def get(self, session_id: str) -> SessionState:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Session not found")
            return session.model_copy(deep=True)

    def mutate(self, session_id: str, callback) -> SessionState:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Session not found")
            callback(session)
            session.updated_at = utc_now()
            return session.model_copy(deep=True)

    def attach_form_template(
        self,
        session_id: str,
        metadata: UploadedFormTemplate,
        schema: FormSchema,
        form: FormState,
        content: bytes,
    ) -> SessionState:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Session not found")
            self._form_templates[session_id] = StoredFormTemplate(
                filename=metadata.filename,
                content=bytes(content),
                schema=schema.model_copy(deep=True),
            )
            session.uploaded_form = metadata
            session.form_schema = schema
            session.form = form
            session.updated_at = utc_now()
            return session.model_copy(deep=True)

    def select_local_schema(
        self, session_id: str, schema: FormSchema, form: FormState
    ) -> SessionState:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Session not found")
            self._form_templates.pop(session_id, None)
            session.uploaded_form = None
            session.form_schema = schema
            session.form = form
            session.updated_at = utc_now()
            return session.model_copy(deep=True)

    def get_form_template(self, session_id: str) -> StoredFormTemplate | None:
        with self._lock:
            if session_id not in self._sessions:
                raise HTTPException(status_code=404, detail="Session not found")
            return self._form_templates.get(session_id)


session_store = SessionStore()
