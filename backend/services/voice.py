from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import ctranslate2 as _ctranslate2  # noqa: F401
import dateparser

# Import CTranslate2 before Paddle initializes its native runtime. On Windows,
# reversing this order can make CTranslate2 fail with WinError 127 because both
# libraries load overlapping native dependencies into the backend process.

NUMBER_WORDS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}


def spoken_digits(value: str) -> str:
    tokens = re.findall(r"[a-z]+|\d+", value.lower())
    converted = [NUMBER_WORDS.get(token, token) for token in tokens]
    if converted and all(token.isdigit() for token in converted):
        return "".join(converted)
    return value.strip()


class LocalIntentParser:
    def parse(self, transcript: str, schema) -> dict[str, Any]:
        cleaned = " ".join(transcript.strip().split())
        lowered = cleaned.lower()
        aliases: list[tuple[str, str]] = []
        for field in schema.fields:
            candidates = [field.label, field.key.replace("_", " "), *field.aliases]
            if field.semantic_key:
                candidates.append(field.semantic_key.replace("_", " "))
            aliases.extend((candidate.lower(), field.key) for candidate in candidates)
        aliases.sort(key=lambda item: len(item[0]), reverse=True)

        if re.search(r"\b(missing|left|incomplete|remaining)\b", lowered):
            return {"intent": "get_missing_fields"}
        if re.search(r"\b(validate|check|verify)\b", lowered):
            return {"intent": "validate_form"}
        if re.search(r"\b(generate|create|download|export)\b.*\b(pdf|form)\b", lowered):
            return {"intent": "generate_completed_pdf"}

        navigation = re.search(
            r"\b(?:go|take|navigate|move)\s+(?:me\s+)?to\s+(?:the\s+)?(.+)", lowered
        )
        if navigation:
            field = self._match_field(navigation.group(1), aliases)
            return {"intent": "navigate_to_field", "field": field}

        update = re.search(
            r"\b(?:change|set|update|correct)\s+(?:my\s+|the\s+)?(.+?)\s+(?:to|as)\s+(.+?)[.]?$",
            cleaned,
            re.IGNORECASE,
        )
        if update:
            field = self._match_field(update.group(1), aliases)
            value = self._normalize_value(field, update.group(2), schema)
            return {"intent": "update_field", "field": field, "value": value}

        return {"intent": "unknown"}

    @staticmethod
    def _match_field(phrase: str, aliases: list[tuple[str, str]]) -> str | None:
        phrase = phrase.lower().strip(" .")
        for alias, key in aliases:
            if alias == phrase or alias in phrase or phrase in alias:
                return key
        return None

    @staticmethod
    def _normalize_value(field_key: str | None, value: str, schema) -> str:
        value = value.strip(" .")
        definition = next(
            (field for field in schema.fields if field.key == field_key), None
        )
        if field_key in {"pincode", "phone_number", "aadhaar_number"}:
            return spoken_digits(value)
        if definition and definition.field_type == "date":
            parsed = dateparser.parse(
                value, settings={"DATE_ORDER": "DMY", "PREFER_DATES_FROM": "past"}
            )
            if parsed:
                return parsed.strftime("%d/%m/%Y")
        if definition and definition.field_type == "select":
            return value.title()
        return value


class FasterWhisperService:
    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self._model = None

    @property
    def model_cached(self) -> bool:
        required = ("config.json", "model.bin", "tokenizer.json")
        return all((self.model_path / filename).is_file() for filename in required)

    def transcribe(self, audio_path: Path) -> str:
        if not self.model_cached:
            raise RuntimeError(
                "Faster-Whisper weights are not cached. Run scripts/cache_models.py while online."
            )
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                str(self.model_path), device="cpu", compute_type="int8"
            )
        segments, _ = self._model.transcribe(
            str(audio_path),
            beam_size=3,
            vad_filter=True,
            condition_on_previous_text=False,
            language="en",
        )
        return " ".join(
            segment.text.strip() for segment in segments if segment.text.strip()
        )
