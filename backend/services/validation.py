from __future__ import annotations

import re
from datetime import UTC, date, datetime

from rapidfuzz.fuzz import ratio, token_sort_ratio

from backend.models import Severity, ValidationIssue

PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def normalize_name(value: str) -> str:
    return " ".join(re.sub(r"[^A-Z ]", " ", value.upper()).split())


def normalize_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def is_valid_pan(value: str) -> bool:
    return bool(PAN_PATTERN.fullmatch(value.strip().upper()))


def _verhoeff_validate(number: str) -> bool:
    multiplication = (
        (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
        (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
        (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
        (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
        (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
        (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
        (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
        (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
        (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
        (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
    )
    permutation = (
        (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
        (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
        (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
        (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
        (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
        (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
        (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
        (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
    )
    checksum = 0
    for index, digit in enumerate(reversed(number)):
        checksum = multiplication[checksum][permutation[index % 8][int(digit)]]
    return checksum == 0


def is_valid_aadhaar(value: str) -> bool:
    digits = normalize_digits(value)
    return len(digits) == 12 and digits[0] not in "01" and _verhoeff_validate(digits)


def is_valid_date(value: str) -> bool:
    value = value.strip()
    for separator in ("/", "-"):
        try:
            parts = [int(part) for part in value.split(separator)]
            if len(parts) != 3:
                continue
            if len(value.split(separator)[0]) == 4:
                parsed = date(parts[0], parts[1], parts[2])
            else:
                parsed = date(parts[2], parts[1], parts[0])
            return 1900 <= parsed.year <= datetime.now(UTC).year
        except (TypeError, ValueError):
            continue
    return False


def validate_form(schema, form, documents) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    definitions = {field.key: field for field in schema.fields}

    def fields_for(semantic_key: str):
        return [
            (definition, form.fields.get(definition.key))
            for definition in schema.fields
            if (definition.semantic_key or definition.key) == semantic_key
        ]

    for key, definition in definitions.items():
        value = form.fields.get(key)
        if definition.required and (value is None or not value.value.strip()):
            issues.append(
                ValidationIssue(
                    code="required",
                    message=f"{definition.label} is required.",
                    severity=Severity.ERROR,
                    field=key,
                )
            )

    for definition, pan in fields_for("pan_number"):
        if pan and pan.value and not is_valid_pan(pan.value):
            issues.append(
                ValidationIssue(
                    code="invalid_pan",
                    message="PAN must contain five letters, four digits, and a final letter.",
                    severity=Severity.ERROR,
                    field=definition.key,
                )
            )

    for definition, aadhaar in fields_for("aadhaar_number"):
        if aadhaar and aadhaar.value and not is_valid_aadhaar(aadhaar.value):
            issues.append(
                ValidationIssue(
                    code="invalid_aadhaar_checksum",
                    message="Aadhaar number failed the Verhoeff checksum.",
                    severity=Severity.ERROR,
                    field=definition.key,
                )
            )

    for definition, dob in fields_for("date_of_birth") + fields_for("driver_date_of_birth"):
        if dob and dob.value and not is_valid_date(dob.value):
            issues.append(
                ValidationIssue(
                    code="invalid_date",
                    message="Date of birth is not a valid past date.",
                    severity=Severity.ERROR,
                    field=definition.key,
                )
            )

    names: list[tuple[str, str]] = []
    motor_roles = any(field.semantic_key == "driver_full_name" for field in schema.fields)
    for document in documents:
        if motor_roles and document.document_type == "insurance_policy":
            # The policyholder and the licensed driver may be different people.
            continue
        if "full_name" in document.fields:
            names.append(
                (document.filename, document.fields["full_name"].normalized_value)
            )
    for index, (left_file, left_name) in enumerate(names):
        for right_file, right_name in names[index + 1 :]:
            normalized_left = normalize_name(left_name)
            normalized_right = normalize_name(right_name)
            similarity = max(
                ratio(normalized_left, normalized_right),
                token_sort_ratio(normalized_left, normalized_right),
            )
            if similarity < 82:
                name_fields = fields_for("full_name")
                issues.append(
                    ValidationIssue(
                        code="name_mismatch",
                        message=(
                            f"Names differ across {left_file} and {right_file} "
                            f"({similarity:.0f}% similarity). Review both sources."
                        ),
                        severity=Severity.WARNING,
                        field=name_fields[0][0].key if name_fields else "full_name",
                        related_documents=[left_file, right_file],
                    )
                )

    for key in ("pan_number", "aadhaar_number", "date_of_birth"):
        values = [
            (document.filename, document.fields[key].normalized_value)
            for document in documents
            if key in document.fields
        ]
        if len({value for _, value in values}) > 1:
            matching_fields = fields_for(key)
            target_definition = matching_fields[0][0] if matching_fields else None
            label = (
                target_definition.label
                if target_definition
                else key.replace("_", " ").title()
            )
            issues.append(
                ValidationIssue(
                    code="cross_document_conflict",
                    message=f"{label} differs across documents. Choose the correct source manually.",
                    severity=Severity.WARNING,
                    field=target_definition.key if target_definition else key,
                    related_documents=[filename for filename, _ in values],
                )
            )
    return issues
