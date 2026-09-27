from __future__ import annotations

import json
from pathlib import Path

from backend.models import FormSchema, FormState, FormValue


class FormSchemaService:
    def __init__(self, schema_dir: Path) -> None:
        self.schema_dir = schema_dir

    def list(self) -> list[FormSchema]:
        schemas = []
        for path in sorted(self.schema_dir.glob("*.json")):
            schemas.append(
                FormSchema.model_validate_json(path.read_text(encoding="utf-8"))
            )
        return schemas

    def get(self, schema_id: str) -> FormSchema:
        path = self.schema_dir / f"{schema_id}.json"
        if not path.is_file():
            raise KeyError(schema_id)
        return FormSchema.model_validate_json(path.read_text(encoding="utf-8"))

    def populate(self, schema: FormSchema, documents) -> FormState:
        values_by_key: dict[str, list[tuple[str, object]]] = {}
        for document in documents:
            for key, extracted in document.fields.items():
                values_by_key.setdefault(key, []).append((document.filename, extracted))

        form_values: dict[str, FormValue] = {}
        for definition in schema.fields:
            candidates = values_by_key.get(definition.key, [])
            normalized = {candidate.normalized_value for _, candidate in candidates}
            if len(normalized) == 1 and candidates:
                filename, candidate = candidates[0]
                evidence = candidate.evidence[0] if candidate.evidence else None
                form_values[definition.key] = FormValue(
                    value=candidate.normalized_value,
                    source="document",
                    source_document_id=evidence.document_id if evidence else None,
                    source_document_name=filename,
                    confidence=evidence.ocr_score if evidence else None,
                )
            elif len(normalized) > 1:
                form_values[definition.key] = FormValue(source="conflict")
            else:
                form_values[definition.key] = FormValue()
        return FormState(schema_id=schema.id, title=schema.title, fields=form_values)

    @staticmethod
    def as_public_summary(schema: FormSchema) -> dict:
        return json.loads(schema.model_dump_json())
