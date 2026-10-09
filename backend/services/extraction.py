from __future__ import annotations

import re
from collections.abc import Iterable
from uuid import uuid4

from backend.models import ExtractedField, FieldEvidence, OCRFragment, ProcessedDocument
from backend.services.field_rules import LABELS, LabelReader, label_pattern
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
        and (
            normalized in re.sub(r"\s", "", fragment.text).lower()
            or (len(fragment.text.strip()) >= 4 and re.sub(r"\s", "", fragment.text).lower() in normalized)
        )
    ]
    return sum(matching) / len(matching) if matching else None


class LocalDocumentExtractor:
    def process(
        self, ocr: OCRResult, filename: str, media_type: str
    ) -> ProcessedDocument:
        document_id = str(uuid4())
        text = ocr.text
        upper = text.upper()
        reader = LabelReader(text, ocr.fragments)
        if "POLICY" in upper and ("INSURANCE" in upper or "INSURED" in upper):
            document_type = "insurance_policy"
        elif re.search(r"DRIVING LICEN[CS]E|\bDL\s*NO\b", upper):
            document_type = "driving_license"
        elif "INCOME TAX" in upper or "PERMANENT ACCOUNT NUMBER" in upper:
            document_type = "pan"
        elif "ELECTION COMMISSION" in upper or "ELECTOR" in upper:
            document_type = "voter_id"
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
        if document_type not in {"insurance_policy", "driving_license"} and (match := AADHAAR_SEARCH.search(text)):
            digits = "".join(match.groups())
            add("aadhaar_number", match.group(0), digits)
        dob = reader.find(LABELS["date_of_birth"], lambda v: bool(DATE_SEARCH.search(v)))
        if dob and (match := DATE_SEARCH.search(dob)):
            add("date_of_birth", match.group(1), match.group(1).replace("-", "/"))

        gender = re.search(r"\b(MALE|FEMALE|TRANSGENDER)\b", upper)
        if gender:
            add("gender", gender.group(1).title(), gender.group(1).lower())

        ordered = "\n".join(f.text for f in reader.fragments) if reader.fragments else "\n".join(reader.lines)
        name = self._extract_name(ordered, document_type)
        labels = LABELS["insured_full_name"] if document_type == "insurance_policy" else LABELS["full_name"]
        name = reader.find(labels, self._is_person_name) or name
        if not name and document_type == "insurance_policy":
            # Some schedules identify the insured with Mr/Mrs rather than a name label.
            for fragment in reader.fragments:
                match = re.match(r"(?i)^(?:mr|mrs|ms)\.?\s+(.+)$", fragment.text.strip())
                if match and self._is_person_name(match[1]):
                    name = match[1]
                    break
        if name:
            add("full_name", name, normalize_name(name).title())
            if document_type == "insurance_policy":
                add("insured_full_name", name, normalize_name(name).title())
            elif document_type == "driving_license":
                add("driver_full_name", name, normalize_name(name).title())

        address_labels = LABELS["insured_address"] if document_type == "insurance_policy" else LABELS["address"]
        address = reader.address(address_labels) or self._extract_address(text, document_type)
        if not address and document_type == "insurance_policy":
            # An unqualified Address label is accepted only in a nearby insured block.
            anchors = [f for f in reader.fragments if label_pattern(LABELS["insured_full_name"]).match(f.text)]
            for anchor in anchors:
                if not anchor.bbox:
                    continue
                height = max(anchor.bbox[3] - anchor.bbox[1], 1)
                nearby = [f for f in reader.fragments if f.bbox and reader._same_region(anchor, f)
                          and 0 <= f.bbox[1] - anchor.bbox[1] <= height * 12]
                address = LabelReader("", nearby).address(LABELS["address"])
                if address:
                    break
        pincode = self._extract_pincode(address or "")
        if pincode:
            add("pincode", pincode, normalize_digits(pincode))

        if address:
            add("address", address, " ".join(address.split()))
            if document_type == "driving_license":
                add("driver_address", address)
            elif document_type == "insurance_policy":
                add("insured_address", address)
            for key, value in self._address_components(address).items():
                add(key, value)

        # PIN can be a separate labelled card line below the address.
        pin = reader.find(LABELS["pincode"], lambda v: bool(re.fullmatch(r"[1-9]\d{5}", v)))
        if not pincode and pin and document_type != "insurance_policy":
            add("pincode", pin)

        for key in ("phone_number", "email", "city", "state"):
            validators = {
                "phone_number": lambda v: bool(re.fullmatch(r"[+\d ()-]{10,20}", v)) and 10 <= len(normalize_digits(v)) <= 13,
                "email": lambda v: bool(re.fullmatch(r"[^\s@*]+@[^\s@*]+\.[A-Za-z]{2,}", v)) and not re.search(r"xx", v, re.IGNORECASE),
                "city": lambda v: bool(re.fullmatch(r"[A-Za-z .'-]{2,50}", v)),
                "state": lambda v: bool(re.fullmatch(r"[A-Za-z .'-]{2,50}", v)),
            }
            value = reader.find(LABELS[key], validators[key])
            if value:
                add(key, value, normalize_digits(value) if key == "phone_number" else value)

        self._extract_motor_fields(reader, document_type, add)
        if document_type == "driving_license" and "date_of_birth" in fields:
            add("driver_date_of_birth", fields["date_of_birth"].value, fields["date_of_birth"].normalized_value)

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
    def _address_components(address: str) -> dict[str, str]:
        result = {}
        # Only explicit city/state names; do not guess a city from an arbitrary noun.
        cities = r"Navi Mumbai|Mumbai|New Delhi|Delhi|Pune|Bengaluru|Bangalore|Chennai|Kolkata|Hyderabad|Ahmedabad|Thane"
        states = r"Maharashtra|Karnataka|Tamil Nadu|Gujarat|Telangana|West Bengal|Uttar Pradesh|Rajasthan|Kerala|Delhi"
        for key, pattern in (("city", cities), ("state", states)):
            if match := re.search(rf"\b({pattern})\b", address, re.IGNORECASE):
                result[key] = match[1].title()
        return result

    def _extract_motor_fields(self, reader, document_type, add):
        identifiers = {
            "policy_number": lambda v: bool(re.fullmatch(r"[A-Za-z0-9 /-]{5,40}", v)) and len(re.sub(r"\D", "", v)) >= 5,
            "client_number": lambda v: bool(re.fullmatch(r"[A-Za-z0-9 -]{4,30}", v)) and bool(re.search(r"\d", v)),
            "vehicle_number": lambda v: bool(re.fullmatch(r"[A-Z]{2}[ -]?\d{1,2}[ -]?[A-Z]{1,3}[ -]?\d{1,4}", v, re.IGNORECASE)),
            "engine_number": lambda v: bool(re.fullmatch(r"[A-Za-z0-9-]{5,30}", v)) and bool(re.search(r"\d", v)),
            "chassis_number": lambda v: bool(re.fullmatch(r"[A-Za-z0-9-]{8,30}", v)) and bool(re.search(r"\d", v)),
            "driving_license_number": lambda v: bool(re.fullmatch(r"[A-Z]{2}[ -]?\d{2}[ -]?\d{7,13}", v, re.IGNORECASE)),
            "current_odometer_reading": lambda v: bool(re.fullmatch(r"\d[\d,]*(?:\s*km)?", v, re.IGNORECASE)),
        }
        for key, accept in identifiers.items():
            if value := reader.find(LABELS[key], accept):
                add(key, value, re.sub(r"\s", "", value).upper())

        for key in ("license_expiry_date", "driver_date_of_birth", "accident_date", "declaration_date"):
            if value := reader.find(LABELS[key], lambda v: bool(DATE_SEARCH.search(v))):
                match = DATE_SEARCH.search(value)
                add(key, match[1], match[1].replace("-", "/"))
        if document_type == "driving_license":
            authority = reader.find(LABELS["license_issuing_authority"], lambda v: bool(re.fullmatch(r"[A-Za-z0-9 ,.-]{2,80}", v)))
            if authority:
                add("license_issuing_authority", authority)
            elif match := re.search(r"(?im)^\s*(?:Issuing Authority\s*[:.]\s*)?(MH\d{2})\s*$", "\n".join(f.text for f in reader.fragments) or "\n".join(reader.lines)):
                add("license_issuing_authority", match[1])
            classes = sorted(set(re.findall(r"\b(?:LMV|MCWG|MCWOG|HMV|HGV|TRANS)\b", "\n".join(reader.lines), re.IGNORECASE)))
            if classes:
                add("authorized_vehicle_type", ", ".join(v.upper() for v in classes))

        for key in ("driver_full_name", "driver_address", "driver_relationship", "under_influence", "authorized_vehicle_type",
                    "accident_place", "accident_description", "occupant_names", "declaration_place"):
            accept = self._is_person_name if key == "driver_full_name" else lambda v: 2 <= len(v) <= 300
            if key == "authorized_vehicle_type":
                accept = lambda v: bool(re.fullmatch(r"(?:LMV|MCWG|MCWOG|HMV|HGV|TRANS|Transport|Motorcycle)(?:[, /]+(?:LMV|MCWG|MCWOG|HMV|HGV|TRANS|Transport|Motorcycle))*", v, re.IGNORECASE))
            if value := reader.find(LABELS[key], accept):
                add(key, value, normalize_name(value).title() if key == "driver_full_name" else value)
        if description := reader.address(LABELS["accident_description"]):
            add("accident_description", description)
        if value := reader.find(LABELS["accident_time"], lambda v: bool(re.fullmatch(r"\d{1,2}:\d{2}(?:\s*[AP]M)?", v, re.IGNORECASE))):
            add("accident_time", re.sub(r"\s*[AP]M$", "", value, flags=re.IGNORECASE))
            if match := re.search(r"[AP]M$", value, re.IGNORECASE):
                add("accident_time_am_pm", match[0].upper())
        for key in ("number_of_occupants", "estimated_repair_cost", "claimed_amount"):
            if value := reader.find(LABELS[key], lambda v: bool(re.fullmatch(r"(?:Rs\.?\s*|INR\s*)?\d[\d,]*(?:\.\d{1,2})?", v, re.IGNORECASE))):
                numeric = re.sub(r"^(?:Rs\.?\s*|INR\s*)", "", value, flags=re.IGNORECASE)
                add(key, value, numeric.replace(",", ""))
        causes = {
            "accident": "accident", "riot": "riot_strike_malicious_act", "theft": "theft_burglary",
            "flood": "flood_storm_tempest", "fire": "fire_explosion_self_ignition",
            "earthquake": "earthquake", "terrorism": "terrorism", "in transit": "in_transit",
        }
        causes.update({value.replace("_", " "): value for value in list(causes.values())})
        def clean_cause(value):
            return " ".join(re.sub(r"[^a-z]+", " ", value.lower()).split())
        if value := reader.find(LABELS["cause_of_damage"], lambda v: clean_cause(v) in causes):
            add(f"cause_{causes[clean_cause(value)]}", "true")
        for key in ("driver_phone_number", "driver_email", "driver_city", "driver_pincode"):
            checks = {
                "driver_phone_number": lambda v: bool(re.fullmatch(r"[+\d ()-]{10,20}", v)) and 10 <= len(normalize_digits(v)) <= 13,
                "driver_email": lambda v: bool(re.fullmatch(r"[^\s@*]+@[^\s@*]+\.[A-Za-z]{2,}", v)) and not re.search(r"xx", v, re.IGNORECASE),
                "driver_city": lambda v: bool(re.fullmatch(r"[A-Za-z .'-]{2,50}", v)),
                "driver_pincode": lambda v: bool(re.fullmatch(r"[1-9]\d{5}", v)),
            }
            if value := reader.find(LABELS[key], checks[key]):
                add(key, value, normalize_digits(value) if key == "driver_phone_number" else value)
        if value := reader.address(LABELS["driver_address"]):
            add("driver_address", value)
            for key, component in self._address_components(value).items():
                add(f"driver_{key}", component)
            if pin := self._extract_pincode(value):
                add("driver_pincode", pin)

    @staticmethod
    def _extract_labeled(text: str, labels: str) -> str | None:
        """Read a complete label, then its same-line or next-line value."""
        match = re.search(
            rf"(?im)^\s*(?:{labels})\s*(?:[:\-]\s*([^\n]+)|\n\s*([^\n]+))",
            text,
        )
        return next((value.strip() for value in match.groups() if value), None) if match else None

    @staticmethod
    def _is_person_name(value: str) -> bool:
        if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,70}", value):
            return False
        return not re.search(
            r"\b(?:government|department|commission|authority|insurance|insurer|"
            r"lombard|company|limited|policy|birth|gender|address|father|mother|"
            r"husband|name|signature|elector|permanent|account|number|india|"
            r"male|female|transgender|proposal|form|vehicle|cover|transcript|details)\b",
            value, re.IGNORECASE,
        )

    @classmethod
    def _extract_name(cls, text: str, document_type: str) -> str | None:
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        labels = (
            r"(?:insured(?: full)? name|name of (?:the )?insured|policy\s*holder(?: name)?)"
            if document_type == "insurance_policy"
            else r"(?:name|full name|name of (?:the )?(?:card holder|elector))"
        )
        labeled = cls._extract_labeled(text, labels)
        if labeled and cls._is_person_name(labeled):
            return labeled
        # Unlabeled cardholder names must be anchored to DOB on Aadhaar,
        # or the PAN number on PAN cards. Never guess from a policy/header.
        for index, line in enumerate(lines):
            anchor = (
                document_type == "aadhaar"
                and re.search(r"\b(?:DOB|date of birth)\b", line, re.IGNORECASE)
            ) or (document_type == "pan" and PAN_SEARCH.fullmatch(line))
            if not anchor:
                continue
            before = (
                lines[max(0, index - 2):index]
                if document_type == "aadhaar"
                else lines[index + 1:index + 2]
            )
            candidates = [value for value in before if cls._is_person_name(value)]
            if len(candidates) == 1:
                return candidates[0]
        return None

    @staticmethod
    def _extract_pincode(text: str) -> str | None:
        matches = PINCODE_SEARCH.findall(text)
        return matches[-1] if matches else None

    @staticmethod
    def _extract_address(text: str, document_type: str) -> str | None:
        labels = (
            r"(?:insured(?: mailing)? address|address of (?:the )?insured|policy\s*holder address)"
            if document_type == "insurance_policy"
            else r"address"
        )
        match = re.search(rf"(?im)^\s*(?:{labels})\s*[:\-]?\s*", text)
        if not match:
            return None
        address_lines = []
        for line in text[match.end():].splitlines():
            line = line.strip()
            if not line or re.search(
                r"^(?:dob|date of birth|gender|name|policy|insurer|registered|"
                r"phone|mobile|email|pan|aadhaar|uid|period|premium)\b",
                line, re.IGNORECASE,
            ) or AADHAAR_SEARCH.search(line):
                break
            address_lines.append(line)
            if PINCODE_SEARCH.search(line) or len(address_lines) >= 4:
                break
        address = " ".join(address_lines)
        return address if 15 <= len(address) <= 220 else None
