"""Shared labels and bounded label/value lookup for cards and motor claims."""
from __future__ import annotations

import re
from collections.abc import Callable

from backend.models import OCRFragment

LABELS = {
    "policy_number": r"policy\s*(?:number|no\.?)",
    "client_number": r"(?:client\s*(?:number|no\.?|id)|customer\s*id)",
    "vehicle_number": r"(?:vehicle\s*(?:registration\s*)?(?:number|no\.?)|registration\s*(?:number|no\.?))",
    "engine_number": r"engine\s*(?:number|no\.?)",
    "chassis_number": r"(?:chassis|chasis)\s*(?:number|no\.?)|vin|vehicle identification number",
    "driving_license_number": r"(?:driving\s*(?:licen[cs]e)\s*(?:number|no\.?)|d\.?\s*l\.?\s*(?:number|no\.?))",
    "license_expiry_date": r"(?:licen[cs]e\s*(?:expiry(?: date)?|valid till)|valid\s*(?:till|until|upto)|expiry date)",
    "license_issuing_authority": r"(?:licen[cs]e\s*)?issuing authority",
    "date_of_birth": r"date of birth|dob|d\.o\.b\.",
    "full_name": r"full name|name|name of (?:the )?(?:card holder|elector)",
    "insured_full_name": r"insured(?: full)? name|name of (?:the )?insured|policy\s*holder(?: name)?",
    "driver_full_name": r"driver(?: full)? name|name of (?:the )?driver",
    "address": r"address|add\.?",
    "insured_address": r"insured(?: mailing)? address|address of (?:the )?insured|policy\s*holder address|communication address|correspondence address",
    "driver_address": r"driver(?: residential)? address|address of (?:the )?driver",
    "driver_date_of_birth": r"driver(?: date of birth| dob)|date of birth of (?:the )?driver",
    "driver_phone_number": r"driver (?:telephone|tel\.?|phone|mobile)(?: number| no\.?)?",
    "driver_email": r"driver email(?: address| id)?",
    "driver_city": r"driver city",
    "driver_pincode": r"driver (?:pin(?:\s*code)?|postcode)",
    "pincode": r"pin(?:\s*code)?|postcode|postal code",
    "phone_number": r"(?:mobile|mob\.?|telephone|tel\.?|phone)(?:\s*(?:number|no\.?))?",
    "email": r"email(?:\s*(?:address|id))?",
    "city": r"city",
    "state": r"state",
    "pan_number": r"pan(?:\s*(?:number|no\.?))?|pan/form\s*97\s*id",
    "current_odometer_reading": r"(?:current\s*)?odometer(?:\s*reading)?",
    "accident_date": r"(?:accident|incident|loss) date|date of (?:the )?(?:accident|incident|loss)",
    "accident_time": r"(?:accident|incident) time|time of (?:the )?(?:accident|incident)",
    "accident_place": r"(?:accident|incident) (?:place|location)|place of (?:the )?(?:accident|incident)",
    "cause_of_damage": r"cause of damage|cause of (?:the )?accident",
    "number_of_occupants": r"(?:number|no\.?) of occupants|occupant count",
    "occupant_names": r"occupant names|names of occupants",
    "estimated_repair_cost": r"estimated (?:cost of repairs|repair cost)|repair estimate",
    "claimed_amount": r"claimed amount|claim amount",
    "accident_description": r"(?:short\s*)?(?:accident|incident) description|description of (?:the )?accident",
    "driver_relationship": r"driver relationship|driver is|relationship to (?:the )?insured",
    "under_influence": r"(?:under|was (?:he|the driver) under) influence(?: of liquor/drugs)?",
    "authorized_vehicle_type": r"authori[sz]ed vehicle (?:type|class)|vehicle class|class of vehicle|cov",
    "declaration_date": r"declaration date",
    "declaration_place": r"declaration place",
}

ALL_LABELS = "|".join(f"(?:{pattern})" for pattern in LABELS.values())
STOP = re.compile(
    rf"^(?:{ALL_LABELS}|doi|s/d/w|father|mother|signature|printed date|"
    r"period|insurance|proposal|issuance|year of manufacture|make|model|registered office|mailing address)\b", re.IGNORECASE,
)


def label_pattern(labels: str):
    return re.compile(rf"^\s*(?:{labels})(?=$|[\s:.-])\s*[:.\-]*\s*(.*)$", re.IGNORECASE)


class LabelReader:
    def __init__(self, text: str, fragments: list[OCRFragment]):
        # OCR sometimes drops punctuation/spaces at familiar label boundaries.
        # Repair labels only; never substitute characters inside identifiers.
        def repair(value):
            value = re.sub(r"(?i)^\s*add(?=flat\b|plot\b|house\b|room\b)", "Add: ", value)
            value = re.sub(r"(?i)^\s*dob(?=\d)", "DOB: ", value)
            return re.sub(r"(?i)^\s*pin(?=[1-9]\d{5}\b)", "PIN: ", value)

        self.fragments = sorted([f.model_copy(update={"text": repair(f.text)}) for f in fragments], key=lambda f: (
            f.page, f.region_id or "", (f.bbox or [0, 0])[1], (f.bbox or [0, 0])[0],
        ))
        self.lines = [repair(line) for line in text.splitlines()]

    @staticmethod
    def _same_region(left, right):
        return left.page == right.page and left.region_id == right.region_id

    def neighbors(self, anchor):
        if not anchor.bbox:
            return []
        x, y, right, bottom = anchor.bbox
        height = max(bottom - y, 1)
        adjacent = []
        for f in self.fragments:
            if f is anchor or not f.bbox or not self._same_region(anchor, f):
                continue
            fx, fy, _, fb = f.bbox
            center_delta = abs((fy + fb - y - bottom) / 2)
            if fx >= right - height and center_delta <= height * 0.7:
                adjacent.append((0, fx - right, f))
            elif 0 < fy - y <= height * 4 and abs(fx - x) <= height * 3:
                adjacent.append((1, fy - y, f))
        return [f for _, _, f in sorted(adjacent, key=lambda item: item[:2])]

    @staticmethod
    def trim(value):
        # A single OCR box can contain several labelled values (e.g. DL No / DOI).
        return re.split(rf"\s+(?:{ALL_LABELS}|doi|bg)\s*[:.]", value, maxsplit=1, flags=re.IGNORECASE)[0].strip(" :.-")

    def find(self, labels: str, accept: Callable[[str], bool]) -> str | None:
        pattern = label_pattern(labels)
        found = []
        located = False
        for anchor in self.fragments:
            match = pattern.match(anchor.text)
            if not match:
                continue
            located = located or anchor.bbox is not None
            inline = self.trim(match[1])
            if inline and accept(inline):
                found.append(inline)
                continue
            neighbors = self.neighbors(anchor)
            # Only the nearest right-hand box or the immediate following line
            # may be the value. Never skip other labels to hunt for a number.
            if neighbors:
                candidate = self.trim(neighbors[0].text)
                if not STOP.match(candidate) and accept(candidate):
                    found.append(candidate)
        if found:
            # Repeated policy schedules are common; different values need review.
            unique = {re.sub(r"\s", "", value).lower() for value in found}
            return found[0] if len(unique) == 1 else None
        if located:
            return None
        for index, line in enumerate(self.lines):
            match = pattern.match(line)
            if not match:
                continue
            value = self.trim(match[1])
            if not value and index + 1 < len(self.lines):
                value = self.trim(self.lines[index + 1])
            if value and not STOP.match(value) and accept(value):
                return value
        return None

    def address(self, labels: str) -> str | None:
        pattern = label_pattern(labels)
        for anchor in self.fragments:
            match = pattern.match(anchor.text)
            if not match or not anchor.bbox:
                continue
            start = self.trim(match[1])
            adjacent = self.neighbors(anchor)
            initial = anchor
            if not start and adjacent:
                height = max(anchor.bbox[3] - anchor.bbox[1], 1)
                choices = [f for f in adjacent if not STOP.match(f.text)
                           and (f.bbox[0] - anchor.bbox[2] <= height * 12)]
                if not choices:
                    continue
                initial = choices[0]
                if STOP.match(initial.text):
                    continue
                start = initial.text.strip()
            if not start:
                continue
            x, y, _, bottom = initial.bbox
            height = max(bottom - y, 1)
            following = [f for f in self.fragments if f is not initial and f.bbox
                         and self._same_region(initial, f)
                         and 0 < f.bbox[1] - y <= height * 9
                         and abs(f.bbox[0] - x) <= height * 3]
            parts = [start]
            for f in following:
                if re.search(r"\b[1-9]\d{5}\b", parts[-1]):
                    break
                if STOP.match(f.text):
                    if label_pattern(LABELS["pincode"]).match(f.text):
                        pin = self.find(LABELS["pincode"], lambda v: bool(re.fullmatch(r"[1-9]\d{5}", v)))
                        if pin:
                            parts.append(pin)
                    break
                if re.search(r"(?<!\d)\d{4}\s+\d{4}\s+\d{4}(?!\d)", f.text):
                    break
                parts.append(f.text.strip())
                if len(parts) >= 8:
                    break
            value = " ".join(parts)
            if 15 <= len(value) <= 300:
                return value
        for index, line in enumerate(self.lines):
            match = pattern.match(line)
            if not match:
                continue
            parts = [match[1].strip()] if match[1].strip() else []
            for following in self.lines[index + 1: index + 9]:
                if parts and re.search(r"\b[1-9]\d{5}\b", parts[-1]):
                    break
                if STOP.match(following) or not following.strip():
                    break
                parts.append(following.strip())
            value = " ".join(parts)
            if 15 <= len(value) <= 300:
                return value
        return None
