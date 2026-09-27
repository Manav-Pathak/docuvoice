from __future__ import annotations

import re
from collections.abc import Iterable
from uuid import uuid4

from backend.models import ExtractedField, FieldEvidence, OCRFragment, ProcessedDocument
from backend.services.ocr import OCRResult
from backend.services.validation import normalize_digits, normalize_name

PAN_SEARCH = re.compile(r"\b[A-Z]{5}\s*[0-9]{4}\s*[A-Z]\b", re.IGNORECASE)
AADHAAR_SEARCH = re.compile(r"(?<!\d)([2-9]\d{3})[\s-]?(\d{4})[\s-]?(\d{4})(?!\d)")
DATE_SEARCH = re.compile(r"\b([0-3]?\d[/-][01]?\d[/-](?:19|20)\d{2})\b")
PINCODE_SEARCH = re.compile(r"(?<!\d)([1-9]\d{5})(?!\d)")


def _average_score(fragments: Iterable[OCRFragment], value: str) -> float | None:
    normalized = re.sub(r"\s", "", value).lower()
    matching = [
        fragment.confidence
        for fragment in fragments
        if fragment.confidence is not None
        and normalized in re.sub(r"\s", "", fragment.text).lower()
    ]
    return sum(matching) / len(matching) if matching else None


class LocalDocumentExtractor:
    def process(
        self, ocr: OCRResult, filename: str, media_type: str
    ) -> ProcessedDocument:
        document_id = str(uuid4())
        text = ocr.text
        upper = text.upper()
        if "INCOME TAX" in upper or "PERMANENT ACCOUNT NUMBER" in upper:
            document_type = "pan"
        elif (
            "AADHAAR" in upper
            or "GOVERNMENT OF INDIA" in upper
            or AADHAAR_SEARCH.search(text)
        ):
            document_type = "aadhaar"
        else:
            document_type = "other_identity"

        fields: dict[str, ExtractedField] = {}

        def add(key: str, value: str, normalized: str | None = None) -> None:
            cleaned = " ".join(value.split())
            score = _average_score(ocr.fragments, cleaned)
            fields[key] = ExtractedField(
                key=key,
                value=cleaned,
                normalized_value=normalized or cleaned,
                evidence=[
                    FieldEvidence(
                        document_id=document_id,
                        document_name=filename,
                        raw_value=cleaned,
                        extraction_method="local_rules",
                        ocr_score=score,
                    )
                ],
            )

        if match := PAN_SEARCH.search(text):
            add("pan_number", match.group(0), re.sub(r"\s", "", match.group(0)).upper())
        if match := AADHAAR_SEARCH.search(text):
            digits = "".join(match.groups())
            add("aadhaar_number", match.group(0), digits)
        if match := DATE_SEARCH.search(text):
            add("date_of_birth", match.group(1), match.group(1).replace("-", "/"))

        gender = re.search(r"\b(MALE|FEMALE|TRANSGENDER)\b", upper)
        if gender:
            add("gender", gender.group(1).title(), gender.group(1).lower())

        name = self._extract_name(text, document_type)
        if name:
            add("full_name", name, normalize_name(name).title())

        pincode = self._extract_pincode(text)
        if pincode:
            add("pincode", pincode, normalize_digits(pincode))

        address = self._extract_address(text)
        if address:
            add("address", address, " ".join(address.split()))

        return ProcessedDocument(
            id=document_id,
            filename=filename,
            media_type=media_type,
            document_type=document_type,
            extraction_method=ocr.method,
            raw_text=text,
            fragments=ocr.fragments,
            fields=fields,
        )

    @staticmethod
    def _extract_name(text: str, document_type: str) -> str | None:
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        label_pattern = re.compile(
            r"^(?:name|name of card holder)\s*[:\-]\s*(.+)$", re.IGNORECASE
        )
        for line in lines:
            if match := label_pattern.match(line):
                return match.group(1).strip()

        ignored = {
            "government of india",
            "income tax department",
            "unique identification authority of india",
            "permanent account number",
        }
        candidates: list[str] = []
        for line in lines:
            lowered = line.lower()
            if any(item in lowered for item in ignored):
                continue
            if (
                PAN_SEARCH.search(line)
                or AADHAAR_SEARCH.search(line)
                or DATE_SEARCH.search(line)
            ):
                continue
            if (
                re.fullmatch(r"[A-Za-z][A-Za-z .'-]{4,60}", line)
                and len(line.split()) >= 2
            ):
                candidates.append(line)
        return candidates[0] if candidates else None

    @staticmethod
    def _extract_pincode(text: str) -> str | None:
        matches = PINCODE_SEARCH.findall(text)
        return matches[-1] if matches else None

    @staticmethod
    def _extract_address(text: str) -> str | None:
        match = re.search(
            r"(?:address|address:|s/o|d/o|w/o)\s*[:\-]?\s*(.{15,220}?)(?=\b\d{4}\s?\d{4}\s?\d{4}\b|$)",
            text.replace("\n", " "),
            re.IGNORECASE,
        )
        return " ".join(match.group(1).split()) if match else None
