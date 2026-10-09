from __future__ import annotations

import json
import re
from pathlib import Path

from backend.models import ExtractedField, FieldOption, FormSchema, FormState, FormValue


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
            source_key = definition.semantic_key or definition.key
            candidates = self._candidates_for(source_key, values_by_key)
            if source_key == "authorized_vehicle_type" and definition.options:
                candidates = self._vehicle_choices(candidates, definition.options)
            elif definition.field_type == "radio" and definition.options:
                candidates = [(name, c.model_copy(update={"normalized_value": option}))
                              for name, c in candidates for option in definition.options
                              if re.sub(r"[^a-z0-9]+", " ", option.lower()).strip() == re.sub(r"[^a-z0-9]+", " ", c.normalized_value.lower()).strip()]
            evidence_by_value = {}
            for _, candidate in candidates:
                evidence_by_value.setdefault(candidate.normalized_value, []).extend(
                    candidate.evidence
                )
            options = [
                FieldOption(value=value, evidence=evidence)
                for value, evidence in evidence_by_value.items()
            ]
            if len(options) == 1 and candidates:
                filename, candidate = candidates[0]
                evidence = candidate.evidence[0] if candidate.evidence else None
                form_values[definition.key] = FormValue(
                    value=candidate.normalized_value,
                    source="document",
                    source_document_id=evidence.document_id if evidence else None,
                    source_document_name=filename,
                    confidence=evidence.ocr_score if evidence else None,
                    options=options,
                )
            elif len(options) > 1:
                form_values[definition.key] = FormValue(
                    source="conflict", options=options
                )
            else:
                form_values[definition.key] = FormValue()
        return FormState(schema_id=schema.id, title=schema.title, fields=form_values)

    @staticmethod
    def _candidates_for(
        source_key: str, values_by_key: dict[str, list[tuple[str, object]]]
    ) -> list[tuple[str, object]]:
        if source_key == "hospital_doctor_name_address":
            result = []
            for filename, candidate in values_by_key.get("provider_name", []):
                doctor = next((c.normalized_value for _, c in values_by_key.get("admitting_doctor_details", [])
                               if c.evidence and candidate.evidence and c.evidence[0].document_id == candidate.evidence[0].document_id), "")
                doctor = re.split(r"\s+-\s+(?:Department|Dept)\b", doctor, maxsplit=1, flags=re.IGNORECASE)[0]
                value = f"{doctor}; {candidate.normalized_value}" if doctor else candidate.normalized_value
                result.append((filename, candidate.model_copy(update={"normalized_value": value})))
            return result
        if source_key == "insured_full_name":
            return values_by_key.get(source_key, []) or values_by_key.get("full_name", [])
        if source_key == "driver_full_name":
            candidates = []
            for filename, candidate in values_by_key.get(source_key, []):
                compact = re.sub(r"\s", "", candidate.normalized_value).lower()
                spelling = [c for _, c in values_by_key.get("full_name", [])
                            if re.sub(r"\s", "", c.normalized_value).lower() == compact]
                best = max(spelling + [candidate], key=lambda c: len(c.normalized_value.split()))
                candidates.append((filename, candidate.model_copy(update={"normalized_value": best.normalized_value})))
            return candidates
        if source_key in {"insured_address", "driver_address"}:
            return values_by_key.get(source_key, []) + values_by_key.get("address", [])
        if source_key in {"driver_city", "driver_pincode", "driver_email", "driver_phone_number"}:
            return values_by_key.get(source_key, []) or values_by_key.get(source_key.removeprefix("driver_"), [])
        name_sources = {
            "first_name": "full_name", "last_name": "full_name",
            "policyholder_first_name": "policyholder_full_name", "policyholder_last_name": "policyholder_full_name",
            "patient_first_name": "patient_full_name", "patient_last_name": "patient_full_name",
        }
        if source_key not in name_sources:
            return values_by_key.get(source_key, [])

        transformed: list[tuple[str, object]] = []
        for filename, candidate in values_by_key.get(name_sources[source_key], []):
            if not isinstance(candidate, ExtractedField):
                continue
            parts = candidate.normalized_value.split()
            if not parts:
                continue
            if source_key.endswith("last_name"):
                value = parts[-1]
            else:
                value = " ".join(parts[:-1]) if len(parts) > 1 else parts[0]
            transformed.append(
                (
                    filename,
                    candidate.model_copy(
                        update={"key": source_key, "value": value, "normalized_value": value}
                    ),
                )
            )
        return transformed

    @staticmethod
    def _vehicle_choices(candidates, options):
        aliases = {"LMV": "lmv", "MCWG": "motorcycle", "MCWOG": "motorcycle", "TRANS": "transport", "HMV": "transport", "HGV": "transport"}
        result = []
        for filename, candidate in candidates:
            classes = {aliases.get(v.strip().upper(), v.strip().lower()) for v in candidate.normalized_value.split(",")}
            for option in options:
                if option.lower() in classes:
                    result.append((filename, candidate.model_copy(update={"normalized_value": option})))
        return result

    @staticmethod
    def as_public_summary(schema: FormSchema) -> dict:
        return json.loads(schema.model_dump_json())
