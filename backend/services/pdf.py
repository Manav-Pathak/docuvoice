from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pymupdf
from pypdf import PdfReader

from backend.models import FormFieldDefinition, FormSchema, FormState
from backend.services.field_rules import LABELS


class AcroFormError(ValueError):
    """Raised when an uploaded PDF cannot be used as an AcroForm template."""


@dataclass(frozen=True)
class AcroFormInspection:
    schema: FormSchema
    page_count: int
    field_count: int


def _plain_name(value: object) -> str:
    return str(value or "").strip().lstrip("/")


def _safe_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not key:
        key = "field"
    if key[0].isdigit():
        key = f"field_{key}"
    return key


def _humanize(value: str) -> str:
    words = re.sub(r"[_\-.]+", " ", value).strip()
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", words)
    return " ".join(words.split()).capitalize() or "Form field"


def _semantic_key(field_name: str, label: str) -> str | None:
    normalized = _safe_key(f"{field_name} {label}")
    tokens = set(normalized.split("_"))
    unrelated_party = {"provider", "hospital", "garage", "surveyor", "third", "party"}

    medical_keys = {
        "card_number": "card_number",
        "policyholder_first_name": "policyholder_first_name",
        "policyholder_last_name": "policyholder_last_name",
        "admitted_person_first_name": "patient_first_name",
        "admitted_person_last_name": "patient_last_name",
        "admitted_dob_day": "patient_dob_day",
        "admitted_dob_month": "patient_dob_month",
        "admitted_dob_year": "patient_dob_year",
        "admitted_age_years": "patient_age_years",
        "loss_treatment_event_admission_date": "admission_date",
        "provider_id": "provider_id", "provider_name": "provider_name",
        "provider_address": "provider_address", "provider_city": "provider_city",
        "provider_state": "provider_state", "provider_pin": "provider_pincode",
        "provisional_diagnosis": "provisional_diagnosis", "treatment_planned": "treatment_planned",
        "estimated_expenses": "estimated_expenses", "estimated_stay_days": "estimated_stay_days",
        "contact_details": "contact_details", "intimating_person_details": "intimating_person_details",
        "admitting_doctor_details": "admitting_doctor_details",
        "hospital_doctor_name_address_line_1": "hospital_doctor_name_address",
        "hospital_city": "provider_city", "hospital_pin": "provider_pincode",
        "medical_cost_breakdown": "medical_cost_breakdown",
        "hospitalization_days": "hospitalization_days", "ambulance_reimbursement": "ambulance_reimbursement",
    }
    if key := medical_keys.get(_safe_key(field_name)):
        return key

    if tokens & unrelated_party or "nominee" in tokens:
        return None
    # Multi-line widgets retain their original names. Populate only the first
    # line until there is an explicit second-line value, never duplicate it.
    if {"2", "3", "second", "third"} & tokens and tokens & {"address", "description"}:
        return None
    if "driver" in tokens and tokens & {"name", "dob", "birth", "address", "city", "pin", "pincode", "email", "telephone", "phone"}:
        for token, key in (("name", "driver_full_name"), ("dob", "driver_date_of_birth"),
                           ("birth", "driver_date_of_birth"), ("address", "driver_address"),
                           ("city", "driver_city"), ("pin", "driver_pincode"),
                           ("pincode", "driver_pincode"), ("email", "driver_email"),
                           ("telephone", "driver_phone_number"), ("phone", "driver_phone_number")):
            if token in tokens:
                return key
    if "insured" in tokens and "name" in tokens:
        return "insured_full_name"
    if "insured" in tokens and "address" in tokens:
        return "insured_address"
    if "city" in tokens:
        return "city"
    if "state" in tokens:
        return "state"
    if "cause" in tokens:
        return _safe_key(field_name) if _safe_key(field_name).startswith("cause_") else None
    if "description" in tokens and "accident" in tokens:
        return "accident_description"
    if {"accident", "time", "am", "pm"} <= tokens:
        return "accident_time_am_pm"
    for key in (
        "policy_number", "client_number", "vehicle_number", "engine_number", "chassis_number",
        "driving_license_number", "license_expiry_date", "license_issuing_authority",
        "current_odometer_reading", "accident_date", "accident_time", "accident_place",
        "number_of_occupants", "occupant_names", "estimated_repair_cost", "claimed_amount",
        "driver_relationship", "under_influence", "authorized_vehicle_type",
        "declaration_date", "declaration_place",
    ):
        words = normalized.replace("_", " ")
        if key in normalized or re.search(rf"\b(?:{LABELS[key]})\b", words, re.IGNORECASE):
            return key

    if {"aadhaar", "aadhar"} & tokens or "uid_number" in normalized:
        return "aadhaar_number"
    if "pan" in tokens and "company" not in tokens:
        return "pan_number"
    if "date_of_birth" in normalized or "birth_date" in normalized or "dob" in tokens:
        return "date_of_birth"
    if "gender" in tokens or "sex" in tokens:
        return "gender"
    if (
        ("pin" in tokens or "pincode" in tokens or "postcode" in tokens)
        and not (tokens & unrelated_party)
    ):
        return "pincode"
    if ("email" in tokens or "emailid" in tokens) and not (tokens & unrelated_party):
        return "email"
    if (
        ("mobile" in tokens or "telephone" in tokens or "phone" in tokens)
        and not (tokens & unrelated_party)
    ):
        return "phone_number"

    excluded_people = unrelated_party | {"nominee"}
    if "name" in tokens and not (tokens & excluded_people):
        if "first" in tokens or "given" in tokens:
            return "first_name"
        if "last" in tokens or "surname" in tokens:
            return "last_name"
        return "full_name"

    excluded_addresses = {"provider", "hospital", "garage", "surveyor"}
    if (
        "address" in tokens
        and not (tokens & excluded_addresses)
        and not ({"2", "3", "second", "third"} & tokens)
    ):
        return "address"
    return None


def _section_for(field_name: str) -> str:
    tokens = set(_safe_key(field_name).split("_"))
    if {"third", "party"} <= tokens or _safe_key(field_name) == "claim_notice_received":
        return "Third-party details"
    if "driver" in tokens or "licence" in tokens or "license" in tokens:
        return "Driver details"
    if tokens & {"incident", "accident", "loss", "theft"}:
        return "Incident details"
    if tokens & {"vehicle", "engine", "chassis", "odometer", "registration"}:
        return "Vehicle details"
    if tokens & {"policy", "claim"}:
        return "Policy and claim details"
    if tokens & {"hospital", "provider", "diagnosis", "treatment", "medical"}:
        return "Medical details"
    if tokens & {"insured", "policyholder", "claimant", "applicant", "patient"}:
        return "Personal details"
    if tokens & {"address", "city", "state", "pin", "pincode", "email", "phone", "mobile"}:
        return "Contact details"
    return "Other details"


def _choice_options(field: object) -> list[str]:
    raw_options = field.get("/Opt", []) if hasattr(field, "get") else []
    options: list[str] = []
    for item in raw_options or []:
        resolved = item.get_object() if hasattr(item, "get_object") else item
        if isinstance(resolved, (list, tuple)) and resolved:
            value = str(resolved[-1])
        else:
            value = str(resolved)
        if value and value not in options:
            options.append(value)
    return options


def _button_options(field: object) -> list[str]:
    options: list[str] = []
    candidates = [field]
    if hasattr(field, "get"):
        candidates.extend(field.get("/Kids", []) or [])
    for candidate in candidates:
        resolved = candidate.get_object() if hasattr(candidate, "get_object") else candidate
        if not hasattr(resolved, "get"):
            continue
        appearance = resolved.get("/AP")
        appearance = appearance.get_object() if hasattr(appearance, "get_object") else appearance
        normal = appearance.get("/N") if hasattr(appearance, "get") else None
        normal = normal.get_object() if hasattr(normal, "get_object") else normal
        if not hasattr(normal, "keys"):
            continue
        for key in normal:
            value = _plain_name(key)
            if value and value.lower() != "off" and value not in options:
                options.append(value)
    return options


def _prepare_boxed_values(values, widgets, schema):
    """Fit export text to comb widgets without changing reviewed source values."""
    definitions = {field.pdf_field_name: field for field in schema.fields}
    for name in list(values):
        widget = widgets.get(name)
        definition = definitions.get(name)
        if not widget or not definition or not widget.field_flags & (1 << 24):
            continue
        semantic = definition.semantic_key
        if semantic == "vehicle_number":
            values[name] = re.sub(r"[^A-Za-z0-9]", "", values[name]).upper()
        elif semantic in {"address", "insured_address", "driver_address"} or "address" in name.lower():
            values[name] = " ".join(re.sub(r"[.\-\u2010-\u2015]", "", values[name]).split())
            match = re.fullmatch(r"(.*?)(line[_ -]?)1", name, re.IGNORECASE)
            continuation = widgets.get(f"{match[1]}{match[2]}2") if match else None
            if (continuation and continuation.field_flags & (1 << 24)
                    and not continuation.field_flags & 1 and continuation.text_maxlen
                    and widget.text_maxlen and len(values[name]) > widget.text_maxlen):
                second_name = continuation.field_name
                if values.get(second_name):
                    raise AcroFormError(
                        f"{definition.label} exceeds its boxes and address line 2 already has text. "
                        "Move the remaining address to line 2 before exporting."
                    )
                text = values[name]
                limit = widget.text_maxlen
                split = text.rfind(" ", 0, limit + 1)
                # Prefer whole words, unless this wastes the capacity needed to fit.
                if split <= 0 or len(text[split:].strip()) > continuation.text_maxlen:
                    split = limit
                values[name] = text[:split].rstrip()
                values[second_name] = text[split:].lstrip()
        else:
            continue
        if widget.text_maxlen and len(values[name]) > widget.text_maxlen:
            raise AcroFormError(f"{definition.label} exceeds the available boxes. Shorten it before exporting.")

    for name, value in values.items():
        widget = widgets.get(name)
        if (widget and "address" in name.lower() and widget.field_flags & (1 << 24)
                and widget.text_maxlen and len(value) > widget.text_maxlen):
            raise AcroFormError("The address exceeds the available boxes on both lines. Shorten it before exporting.")


class PDFGenerationService:
    """Inspects uploaded AcroForms and fills the original PDF template."""

    def inspect_acroform(self, content: bytes, filename: str) -> AcroFormInspection:
        try:
            reader = PdfReader(BytesIO(content), strict=False)
            if reader.is_encrypted and reader.decrypt("") == 0:
                raise AcroFormError("Password-protected PDF forms are not supported.")
            raw_fields = reader.get_fields() or {}
        except AcroFormError:
            raise
        except Exception as exc:
            raise AcroFormError("The uploaded PDF could not be opened.") from exc

        if not raw_fields:
            raise AcroFormError(
                "This PDF has no AcroForm fields. Upload a fillable PDF form."
            )

        definitions: list[FormFieldDefinition] = []
        used_keys: set[str] = set()
        for pdf_name, field in raw_fields.items():
            name = str(pdf_name).strip()
            if not name:
                continue
            field_type_code = _plain_name(field.get("/FT"))
            flags = int(field.get("/Ff", 0) or 0)
            read_only = bool(flags & 1)
            required = bool(flags & 2)

            if field_type_code == "Tx":
                field_type = "textarea" if flags & (1 << 12) else "text"
                options: list[str] = []
            elif field_type_code == "Ch":
                field_type = "select"
                options = _choice_options(field)
            elif field_type_code == "Btn":
                if flags & (1 << 16):
                    continue
                field_type = "radio" if flags & (1 << 15) else "checkbox"
                options = _button_options(field)
            else:
                # Signatures and unknown controls are not safe to populate as text.
                continue

            tooltip = str(field.get("/TU") or field.get("/TM") or "").strip()
            label = tooltip or _humanize(name)
            semantic_key = _semantic_key(name, label)
            if semantic_key in {"date_of_birth", "driver_date_of_birth", "license_expiry_date", "accident_date", "declaration_date", "admission_date"} and field_type == "text":
                field_type = "date"
            elif semantic_key == "email" and field_type == "text":
                field_type = "email"

            key = _safe_key(name)
            base_key = key
            suffix = 2
            while key in used_keys:
                key = f"{base_key}_{suffix}"
                suffix += 1
            used_keys.add(key)

            max_length = field.get("/MaxLen")
            definitions.append(
                FormFieldDefinition(
                    key=key,
                    label=label,
                    section=_section_for(name),
                    field_type=field_type,
                    required=required,
                    aliases=[name, label],
                    semantic_key=semantic_key,
                    pdf_field_name=name,
                    options=options,
                    read_only=read_only,
                    max_length=int(max_length) if max_length is not None else None,
                )
            )

        if not definitions:
            raise AcroFormError(
                "The PDF contains no supported text, choice, checkbox, or radio fields."
            )

        # The first review input holds the full address; PDF limits apply after
        # punctuation removal and continuation into the second export widget.
        names = {field.pdf_field_name for field in definitions if not field.read_only}
        for definition in definitions:
            name = definition.pdf_field_name or ""
            match = re.fullmatch(r"(.*?)(line[_ -]?)1", name, re.IGNORECASE)
            if (definition.semantic_key in {"address", "insured_address", "driver_address"}
                    and match and f"{match[1]}{match[2]}2" in names) or definition.semantic_key == "vehicle_number":
                definition.max_length = None

        digest = hashlib.sha256(content).hexdigest()[:12]
        title = _humanize(Path(filename).stem)
        schema = FormSchema(
            id=f"uploaded_{digest}",
            title=title,
            description=f"AcroForm schema discovered locally from {filename}",
            fields=definitions,
        )
        return AcroFormInspection(
            schema=schema,
            page_count=len(reader.pages),
            field_count=len(definitions),
        )

    def fill_acroform(
        self, content: bytes, schema: FormSchema, form: FormState
    ) -> bytes:
        def export_value(definition):
            value = form.fields[definition.key].value
            if (definition.field_type == "date" and "DDMMYYYY" in definition.label.upper()
                    and re.fullmatch(r"\d{2}[/-]\d{2}[/-]\d{4}", value)):
                return re.sub(r"\D", "", value)
            return value

        values = {
            definition.pdf_field_name: export_value(definition)
            for definition in schema.fields
            if definition.pdf_field_name
            and not definition.read_only
            and definition.key in form.fields
            and form.fields[definition.key].value != ""
        }
        try:
            document = pymupdf.open(stream=content, filetype="pdf")
        except Exception as exc:
            raise AcroFormError("The stored form template could not be reopened.") from exc

        updated_fields: set[str] = set()
        try:
            widget_map = {widget.field_name: widget for page in document for widget in page.widgets() or []}
            _prepare_boxed_values(values, widget_map, schema)
            for page in document:
                widgets = list(page.widgets() or [])
                for widget in widgets:
                    name = widget.field_name
                    if name not in values:
                        continue
                    value = values[name]
                    if widget.field_type in {
                        pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
                        pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
                    }:
                        states = widget.button_states() or {}
                        available = [
                            str(item)
                            for item in states.get("normal", [])
                            if str(item).lower() != "off"
                        ]
                        truthy = value.strip().lower() in {
                            "1",
                            "true",
                            "yes",
                            "on",
                            "checked",
                        }
                        if value in available:
                            widget.field_value = value
                        elif truthy and available:
                            widget.field_value = available[0]
                        else:
                            widget.field_value = "Off"
                    else:
                        widget.field_value = value
                        if name in {"provisional_diagnosis", "treatment_planned", "medical_cost_breakdown",
                                    "intimating_person_details", "admitting_doctor_details"}:
                            # Fit complete medical narratives into the template's short rows.
                            font = pymupdf.Font("helv")
                            size = min(widget.text_fontsize or 9, 9)
                            while size > 5:
                                lines, width = 1, 0.0
                                for word in value.split():
                                    word_width = font.text_length(word + " ", fontsize=size)
                                    if width and width + word_width > widget.rect.width - 4:
                                        lines += 1
                                        width = 0.0
                                    width += word_width
                                if lines * size * 1.3 + 4 <= widget.rect.height:
                                    break
                                size -= 0.5
                            widget.text_fontsize = size
                    widget.update()
                    updated_fields.add(name)

            missing = sorted(set(values) - updated_fields)
            if missing:
                raise AcroFormError(
                    "Some form fields could not be located in the PDF: "
                    + ", ".join(missing[:5])
                )
            output = BytesIO()
            document.save(output, garbage=4, deflate=True)
            return output.getvalue()
        except AcroFormError:
            raise
        except Exception as exc:
            raise AcroFormError("The AcroForm could not be filled.") from exc
        finally:
            document.close()

    def generate(self, schema: FormSchema, form: FormState) -> bytes:
        """Legacy fallback for the old preset-schema API."""
        document = pymupdf.open()
        page = document.new_page(width=595, height=842)
        navy = (0.07, 0.15, 0.24)
        teal = (0.02, 0.55, 0.49)
        page.draw_rect((0, 0, 595, 90), color=navy, fill=navy)
        page.insert_text((42, 42), "DOCUVOICE", fontsize=11, color=(0.75, 0.95, 0.91))
        page.insert_text((42, 70), schema.title, fontsize=19, color=(1, 1, 1))

        y = 120
        current_section = None
        for definition in schema.fields:
            if definition.section != current_section:
                current_section = definition.section
                if y > 735:
                    page = document.new_page(width=595, height=842)
                    y = 55
                page.insert_text((42, y), current_section.upper(), fontsize=9, color=teal)
                y += 23
            value = form.fields[definition.key].value or "Not provided"
            page.insert_text((42, y), definition.label, fontsize=8, color=(0.35, 0.4, 0.45))
            page.insert_textbox((190, y - 10, 550, y + 22), value, fontsize=10, color=navy)
            page.draw_line((42, y + 16), (550, y + 16), color=(0.88, 0.9, 0.91), width=0.5)
            y += 36
            if y > 780:
                page = document.new_page(width=595, height=842)
                y = 55
                current_section = None

        output = BytesIO()
        document.save(output)
        document.close()
        return output.getvalue()
