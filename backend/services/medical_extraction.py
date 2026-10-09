"""Labelled admission notes and provisional hospital estimates."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from backend.services.field_rules import LABELS


def parse_date(value: str) -> str | None:
    patterns = (
        (r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", ("%d/%m/%Y", "%d-%m-%Y")),
        (r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b", ("%d %B %Y", "%d %b %Y")),
    )
    for pattern, formats in patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        for fmt in formats:
            try:
                return datetime.strptime(match[0], fmt).replace(tzinfo=UTC).strftime("%d/%m/%Y")
            except ValueError:
                continue
    return None


def money(value: str) -> str | None:
    match = re.fullmatch(r"(?:INR\s*|Rs\.?\s*|₹\s*)?(\d[\d,]*(?:\.\d{1,2})?)", value.strip(), re.IGNORECASE)
    return match[1].replace(",", "") if match else None


def extract_medical(reader, add, address_components):
    def find(key, accept=lambda value: bool(value.strip())):
        return reader.find(LABELS[key], accept)

    for key in ("patient_full_name", "policyholder_full_name"):
        value = find(key, lambda v: bool(re.fullmatch(r"[A-Za-z .'-]+(?:\s*\(self\))?", v, re.IGNORECASE)))
        if value:
            value = re.sub(r"\s*\(self\)\s*$", "", value, flags=re.IGNORECASE)
            add(key, value, value.title())

    dob_age = find("patient_dob_age", lambda v: parse_date(v) is not None)
    if dob_age:
        dob = parse_date(dob_age)
        add("patient_date_of_birth", dob)
        for key, value in zip(("patient_dob_day", "patient_dob_month", "patient_dob_year"), dob.split("/"), strict=True):
            add(key, value)
        if match := re.search(r"\b(\d{1,3})\s*years?\b", dob_age, re.IGNORECASE):
            add("patient_age_years", match[1])

    address = reader.block(LABELS["patient_address"])
    components = find("patient_city_state_pin", lambda v: bool(re.search(r"\b[1-9]\d{5}\b", v)))
    if components:
        parts = [p.strip() for p in components.split("/")]
        if len(parts) == 3:
            for key, value in zip(("city", "state", "pincode"), parts, strict=True):
                add(key, value)
        if address:
            address = f"{address}, {components.replace(' / ', ', ')}"
    if address:
        add("address", address)
        for key, value in address_components(address).items():
            add(key, value)
        if match := re.search(r"\b([1-9]\d{5})\b", address):
            add("pincode", match[1])

    combined_policy = find("policy_card_number", lambda v: "/" in v)
    if combined_policy:
        policy, card = combined_policy.rsplit("/", 1)
        if policy.strip() and card.strip():
            add("policy_number", policy.strip())
            add("card_number", card.strip())
    elif value := find("card_number"):
        add("card_number", value)

    phone = find("patient_contact", lambda v: bool(re.fullmatch(r"[+\d ()-]{10,20}", v)))
    if phone:
        add("contact_details", phone, re.sub(r"\D", "", phone))

    for key in ("admission_date", "document_date_place"):
        value = find(key, lambda v: parse_date(v) is not None)
        if value:
            date = parse_date(value)
            add("admission_date" if key == "admission_date" else "declaration_date", date)
            if key == "document_date_place" and "/" in value:
                place = value.rsplit("/", 1)[1].strip()
                if re.fullmatch(r"[A-Za-z .'-]+", place):
                    add("declaration_place", place)

    for key in ("provisional_diagnosis", "treatment_planned", "admitting_doctor_details"):
        if value := reader.block(LABELS[key]):
            add(key, value)

    provider = find("hospital_provider", lambda v: "/" in v)
    if provider:
        name, identifier = provider.rsplit("/", 1)
        add("provider_name", name.strip())
        add("provider_id", identifier.strip())
    else:
        for key in ("provider_name", "provider_id"):
            if value := find(key):
                add(key, value)
    if value := reader.block(LABELS["provider_address"]):
        add("provider_address", value)
        for key, component in address_components(value).items():
            add(f"provider_{key}", component)
        if match := re.search(r"\b([1-9]\d{5})\b", value):
            add("provider_pincode", match[1])
    for key in ("provider_city", "provider_state", "provider_pincode"):
        if value := find(key):
            add(key, value)

    for key in ("estimated_expenses", "ambulance_reimbursement"):
        if value := find(key, lambda v: money(v) is not None):
            add(key, value, money(value))
    for key in ("estimated_stay_days", "hospitalization_days"):
        if value := find(key, lambda v: bool(re.match(r"\d+\s*(?:days?\b|$)", v, re.IGNORECASE))):
            add(key, value, re.match(r"\d+", value)[0])

    contact = find("intimating_person_details")
    phone = find("intimating_phone", lambda v: bool(re.fullmatch(r"[+\d ()-]{10,20}", v)))
    if contact:
        add("intimating_person_details", contact + (f"; Phone: {phone}" if phone else ""))

    breakdown = reader.block(LABELS["medical_cost_breakdown"])
    if not breakdown:
        # Match item/amount rows by coordinates, ignoring the middle Basis column.
        headers = [f for f in reader.fragments if f.text.lower() == "item" and f.bbox]
        totals = [f for f in reader.fragments if re.match(LABELS["estimated_expenses"], f.text, re.IGNORECASE) and f.bbox]
        rows = []
        for header in headers:
            limits = [f.bbox[1] for f in totals if reader._same_region(header, f) and f.bbox[1] > header.bbox[1]]
            if not limits:
                continue
            for item in reader.fragments:
                if not item.bbox or not reader._same_region(header, item):
                    continue
                if not (header.bbox[1] < item.bbox[1] < min(limits) and abs(item.bbox[0] - header.bbox[0]) < 15):
                    continue
                amounts = [f for f in reader.fragments if f.bbox and reader._same_region(item, f)
                           and abs(f.bbox[1] - item.bbox[1]) < (item.bbox[3] - item.bbox[1]) * 0.7
                           and f.bbox[0] > item.bbox[2] and money(f.text) is not None]
                if amounts:
                    amount = max(amounts, key=lambda f: f.bbox[0])
                    rows.append(f"{item.text}: {money(amount.text)}")
        breakdown = "; ".join(rows)
    if breakdown:
        add("medical_cost_breakdown", breakdown)
