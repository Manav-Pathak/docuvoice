from __future__ import annotations

from threading import RLock
from uuid import uuid4

from fastapi import HTTPException

from backend.models import OperatingMode, SessionState, utc_now


class SessionStore:
    """Thread-safe application-owned state for the local demo.

    The interface deliberately hides storage details so a durable local store can
    replace this in-memory implementation without touching routes or services.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
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


session_store = SessionStore()
