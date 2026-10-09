from io import BytesIO

import pymupdf
from fastapi.testclient import TestClient
from pypdf import PdfReader

from backend.app import app

client = TestClient(app)


def test_blank_field_update_and_cleared_value_are_not_user_confirmation():
    session_id = client.post("/api/sessions", json={"mode": "offline"}).json()["id"]
    client.post(f"/api/sessions/{session_id}/form", json={"schema_id": "personal_accident_claim"})
    path = f"/api/sessions/{session_id}/fields/full_name"
    blank = client.patch(path, json={"value": "  "}).json()["form"]["fields"]["full_name"]
    assert blank["source"] == "empty"
    edited = client.patch(path, json={"value": "Aarav Sharma"}).json()["form"]["fields"]["full_name"]
    assert edited["source"] == "user"
    cleared = client.patch(path, json={"value": ""}).json()["form"]["fields"]["full_name"]
    assert cleared["source"] == "empty"
    assert cleared["source_document_name"] is None


def test_clearing_selected_document_value_restores_unresolved_conflict():
    session_id = client.post("/api/sessions", json={"mode": "offline"}).json()["id"]
    for index, name in enumerate(("AARAV SHARMA", "RAHUL SHARMA")):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((50, 50), f"GOVERNMENT OF INDIA\nName: {name}\nDOB: 14/08/1999")
        content = document.tobytes()
        document.close()
        response = client.post(
            f"/api/sessions/{session_id}/documents",
            files={"file": (f"synthetic-{index}.pdf", content, "application/pdf")},
        )
        assert response.status_code == 200
    client.post(f"/api/sessions/{session_id}/form", json={"schema_id": "personal_accident_claim"})
    path = f"/api/sessions/{session_id}/fields/full_name"
    selected = client.patch(path, json={"value": "Aarav Sharma"}).json()["form"]["fields"]["full_name"]
    assert selected["source_document_name"] == "synthetic-0.pdf"
    cleared = client.patch(path, json={"value": ""}).json()["form"]["fields"]["full_name"]
    assert cleared["source"] == "conflict"
    assert len(cleared["options"]) == 2
    assert cleared["source_document_name"] is None


def make_test_acroform() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((50, 60), "Test insurance form", fontsize=16)
    fields = [
        ("insured_name", "Insured name", pymupdf.Rect(50, 90, 350, 115), None),
        ("insured_pan", "PAN number", pymupdf.Rect(50, 135, 220, 160), 10),
        ("aadhaar_number", "Aadhaar number", pymupdf.Rect(50, 180, 250, 205), 12),
        ("date_of_birth", "Date of birth", pymupdf.Rect(50, 225, 220, 250), 10),
    ]
    for name, label, rect, max_length in fields:
        widget = pymupdf.Widget()
        widget.field_name = name
        widget.field_label = label
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = rect
        widget.text_font = "Helv"
        widget.text_fontsize = 10
        if max_length:
            widget.text_maxlen = max_length
        page.add_widget(widget)
    output = BytesIO()
    document.save(output)
    document.close()
    return output.getvalue()


def test_synthetic_offline_flow_to_pdf():
    session = client.post("/api/sessions", json={"mode": "offline"}).json()
    session_id = session["id"]

    response = client.post(f"/api/sessions/{session_id}/demo")
    assert response.status_code == 200
    assert response.json()["documents"][0]["extraction_method"] == "synthetic_demo"

    response = client.post(
        f"/api/sessions/{session_id}/form",
        json={"schema_id": "personal_accident_claim"},
    )
    assert response.status_code == 200
    assert response.json()["form"]["fields"]["full_name"]["value"] == "Aarav Sharma"

    response = client.post(
        f"/api/sessions/{session_id}/voice/command",
        json={"transcript": "Change my pincode to four zero zero zero five six"},
    )
    assert response.status_code == 200
    assert response.json()["session"]["form"]["fields"]["pincode"]["value"] == "400056"

    response = client.get(f"/api/sessions/{session_id}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_online_mode_failure_preserves_offline_state():
    session = client.post("/api/sessions", json={"mode": "offline"}).json()
    response = client.patch(
        f"/api/sessions/{session['id']}/mode", json={"mode": "online"}
    )
    if response.status_code == 409:
        preserved = client.get(f"/api/sessions/{session['id']}").json()
        assert preserved["mode"] == "offline"


def test_uploaded_acroform_is_discovered_mapped_and_filled_in_place():
    session = client.post("/api/sessions", json={"mode": "offline"}).json()
    session_id = session["id"]
    client.post(f"/api/sessions/{session_id}/demo")

    response = client.post(
        f"/api/sessions/{session_id}/form-template",
        files={"file": ("test-insurance.pdf", make_test_acroform(), "application/pdf")},
    )
    assert response.status_code == 200
    state = response.json()
    assert state["uploaded_form"]["field_count"] == 4
    assert state["form_schema"]["fields"][0]["pdf_field_name"] == "insured_name"
    assert state["form"]["fields"]["insured_name"]["value"] == "Aarav Sharma"
    assert state["form"]["fields"]["insured_pan"]["value"] == "ABCPK1234F"
    assert state["form"]["fields"]["aadhaar_number"]["value"] == "999999990019"

    exported = client.get(f"/api/sessions/{session_id}/export")
    assert exported.status_code == 200
    assert "completed-test-insurance.pdf" in exported.headers["content-disposition"]
    fields = PdfReader(BytesIO(exported.content)).get_fields()
    assert fields["insured_name"].get("/V") == "Aarav Sharma"
    assert fields["insured_pan"].get("/V") == "ABCPK1234F"
    assert fields["aadhaar_number"].get("/V") == "999999990019"
    assert fields["date_of_birth"].get("/V") == "14/08/1999"


def test_flat_pdf_is_rejected_without_destroying_session_state():
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 50), "This is not an AcroForm")
    flat_pdf = document.tobytes()
    document.close()

    session = client.post("/api/sessions", json={"mode": "offline"}).json()
    session_id = session["id"]
    client.post(f"/api/sessions/{session_id}/demo")
    response = client.post(
        f"/api/sessions/{session_id}/form-template",
        files={"file": ("flat.pdf", flat_pdf, "application/pdf")},
    )
    assert response.status_code == 422
    preserved = client.get(f"/api/sessions/{session_id}").json()
    assert len(preserved["documents"]) == 2
    assert preserved["uploaded_form"] is None
