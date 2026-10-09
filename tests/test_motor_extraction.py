from pathlib import Path

from backend.models import FormFieldDefinition, FormSchema, OCRFragment
from backend.services.extraction import LocalDocumentExtractor
from backend.services.forms import FormSchemaService
from backend.services.ocr import OCRResult
from backend.services.pdf import _semantic_key


def extract(text, fragments=None):
    return LocalDocumentExtractor().process(
        OCRResult(text, fragments or [], "test"), "synthetic.txt", "text/plain",
    )


def fragment(text, x, y, width=180, region="details"):
    return OCRFragment(text=text, page=1, bbox=[x, y, x + width, y + 12], confidence=0.95, region_id=region)


def test_shuffled_multicolumn_policy_pairs_values_by_geometry():
    fragments = [
        fragment("Motor Insurance Policy", 0, 0),
        fragment("Insured Name", 0, 20), fragment("AARAV SHARMA", 240, 20),
        fragment("Engine No.", 0, 40), fragment("ENG123456", 240, 40),
        fragment("Chasis No.", 0, 60), fragment("CHS123456789", 240, 60),
        fragment("Registration No.", 0, 80), fragment("MH02AB1234", 240, 80),
        fragment("Policy No.", 0, 100), fragment("3001/123456789/01/000", 240, 100),
        fragment("Insured Address: 21 Lotus Residency", 0, 120),
        fragment("Mumbai, Maharashtra 400056", 0, 140),
        fragment("Premium: 9999", 0, 160),
    ]
    fragments = fragments[::-1]
    document = extract("\n".join(f.text for f in fragments), fragments)
    assert document.fields["insured_full_name"].normalized_value == "Aarav Sharma"
    assert document.fields["engine_number"].value == "ENG123456"
    assert document.fields["chassis_number"].value == "CHS123456789"
    assert document.fields["vehicle_number"].value == "MH02AB1234"
    assert document.fields["policy_number"].value == "3001/123456789/01/000"
    assert document.fields["pincode"].value == "400056"
    assert document.fields["city"].value == "Mumbai"


def test_license_expiry_classes_and_joined_address_labels():
    fragments = [
        fragment("MOTOR DRIVING LICENCE", 0, 0),
        fragment("DL No: MH47 20230001234", 0, 20),
        fragment("Valid Till: 03-12-2044 (NT)", 0, 40),
        fragment("COV", 0, 60), fragment("DOI", 240, 60),
        fragment("LMV", 0, 80), fragment("24-01-2023", 240, 80),
        fragment("MCWG", 0, 100), fragment("24-01-2023", 240, 100),
        fragment("DOB: 04-12-2004", 0, 120),
        fragment("Name: AARAV SHARMA", 0, 140),
        fragment("S/D/W of: RAHUL SHARMA", 0, 160),
        fragment("AddFlat No.21, Lotus Residency", 0, 180),
        fragment("Kandivali West, Mumbai Maharashtra", 0, 200),
        fragment("PIN400056", 0, 220),
        fragment("Signature & ID of", 0, 240),
        fragment("Issuing Authority:", 0, 260), fragment("MH47", 0, 280),
    ]
    document = extract("\n".join(f.text for f in fragments[::-1]), fragments[::-1])
    assert document.document_type == "driving_license"
    assert document.fields["driver_full_name"].value == "AARAV SHARMA"
    assert document.fields["driver_date_of_birth"].normalized_value == "04/12/2004"
    assert document.fields["license_expiry_date"].normalized_value == "03/12/2044"
    assert document.fields["driving_license_number"].normalized_value == "MH4720230001234"
    assert document.fields["authorized_vehicle_type"].value == "LMV, MCWG"
    assert document.fields["license_issuing_authority"].value == "MH47"
    assert document.fields["driver_address"].value.endswith("400056")
    assert "Signature" not in document.fields["driver_address"].value


def test_aadhaar_address_stops_at_pin_and_ignores_print_date():
    document = extract("""AADHAAR
Address: Flat No.21, Lotus Residency
Gokhale Road, Kandivali West
Mumbai, Maharashtra, 400056
Printed Date: 23/01/2023
9999 9999 0019""")
    assert document.fields["address"].value.endswith("400056")
    assert "Printed" not in document.fields["address"].value
    assert "date_of_birth" not in document.fields


def test_missing_vehicle_value_does_not_steal_next_field_or_other_region():
    fragments = [fragment("Insurance Policy", 0, 0), fragment("Engine No.", 0, 20),
                 fragment("Chassis No.", 0, 40), fragment("CHS123456789", 240, 40),
                 fragment("ENG999999", 240, 20, region="footer")]
    document = extract("\n".join(f.text for f in fragments), fragments)
    assert "engine_number" not in document.fields
    assert document.fields["chassis_number"].value == "CHS123456789"


def test_accident_note_extracts_explicit_claim_fields_not_policy_dates():
    document = extract("""Driver and Accident Details
Driver Name: AARAV SHARMA
Accident Date: 07/10/2026
Accident Time: 09:30 PM
Accident Place: Mumbai
Cause of Damage: Accident
Number of Occupants: 2
Occupant Names: Aarav Sharma, Rahul Sharma
Estimated Repair Cost: Rs. 12,500
Accident Description: Rear bumper damaged while parking.
Driver Relationship: Owner
Under Influence: No
Declaration Date: 08/10/2026
Declaration Place: Mumbai""")
    assert document.fields["accident_date"].value == "07/10/2026"
    assert document.fields["accident_time"].value == "09:30"
    assert document.fields["accident_time_am_pm"].value == "PM"
    assert document.fields["estimated_repair_cost"].normalized_value == "12500"
    assert document.fields["cause_accident"].value == "true"
    assert document.fields["driver_full_name"].value == "AARAV SHARMA"
    assert "date_of_birth" not in document.fields


def test_form_mapping_keeps_names_separate_and_offers_address_options():
    policy = extract("""Motor Insurance Policy
Insured Name: RAHUL SHARMA
Insured Address: 21 Lotus Residency, Mumbai 400056""")
    licence = extract("""MOTOR DRIVING LICENCE
Name: AARAV SHARMA
Address: 45 Rose Residency, Mumbai 400067
DOB: 04/12/2004
LMV MCWG""")
    schema = FormSchema(id="test", title="Car", description="Test", fields=[
        FormFieldDefinition(key=key, label=key, section="Test", semantic_key=_semantic_key(key, key))
        for key in ["insured_name", "driver_name", "insured_address_line_1", "driver_address_line_1", "third_party_address_line_1", "driver_dob"]
    ])
    form = FormSchemaService(Path("unused")).populate(schema, [policy, licence])
    assert form.fields["insured_name"].value == "Rahul Sharma"
    assert form.fields["driver_name"].value == "Aarav Sharma"
    assert form.fields["insured_address_line_1"].source == "conflict"
    assert form.fields["driver_address_line_1"].source == "conflict"
    assert len(form.fields["driver_address_line_1"].options) == 2
    assert form.fields["third_party_address_line_1"].value == ""
    assert form.fields["driver_dob"].value == "04/12/2004"


def test_masked_contacts_and_insurer_addresses_are_not_personal_values():
    document = extract("""Motor Insurance Policy
Address: Insurer House, Mumbai 400025
Insured Name: AARAV SHARMA
Mobile No: 99******69
Email ID: aa****@example.com
Policy No: 3001/123456789/01/000""")
    assert "address" not in document.fields
    assert "phone_number" not in document.fields
    assert "email" not in document.fields


def test_vehicle_class_options_require_selection_when_multiple_classes_apply():
    licence = extract("MOTOR DRIVING LICENCE\nLMV\nMCWG")
    schema = FormSchema(id="test", title="Car", description="Test", fields=[
        FormFieldDefinition(key="authorized_vehicle_type", label="Vehicle type", section="Driver", field_type="radio", options=["LMV", "Motorcycle", "Transport"]),
    ])
    form = FormSchemaService(Path("unused")).populate(schema, [licence])
    assert form.fields["authorized_vehicle_type"].source == "conflict"
    assert {v.value for v in form.fields["authorized_vehicle_type"].options} == {"LMV", "Motorcycle"}


def test_coordinates_prevent_cross_image_pairing_even_when_text_is_adjacent():
    fragments = [fragment("Engine No.", 0, 20), fragment("ENG999999", 240, 20, region="footer")]
    document = extract("Insurance Policy\nEngine No.\nENG999999", fragments)
    assert "engine_number" not in document.fields


def test_pdf_export_preserves_motor_values_and_radio_selections():
    from io import BytesIO

    import pymupdf
    from pypdf import PdfReader

    from backend.services.pdf import PDFGenerationService

    source = extract("""Motor Insurance Policy
Insured Name: RAHUL SHARMA
Engine No.: ENG123456
Chassis No.: CHS123456789
Driver Name: AARAV SHARMA
Accident Date: 07/10/2026
Estimated Repair Cost: Rs. 12,500
Cause of Damage: Accident""")
    pdf = pymupdf.open()
    page = pdf.new_page()
    for i, key in enumerate(["insured_name", "driver_name", "insured_engine_number", "insured_chassis_number", "accident_date", "estimated_repair_cost", "cause_accident"]):
        widget = pymupdf.Widget()
        widget.field_name = key
        if key == "accident_date":
            widget.field_label = "Accident Date (DDMMYYYY)"
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX if key == "cause_accident" else pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = pymupdf.Rect(50, 50 + i * 30, 250, 75 + i * 30)
        page.add_widget(widget)
    content = pdf.tobytes()
    pdf.close()
    service = PDFGenerationService()
    schema = service.inspect_acroform(content, "synthetic-car.pdf").schema
    form = FormSchemaService(Path("unused")).populate(schema, [source])
    exported = PdfReader(BytesIO(service.fill_acroform(content, schema, form))).get_fields()
    assert exported["insured_name"]["/V"] == "Rahul Sharma"
    assert exported["driver_name"]["/V"] == "Aarav Sharma"
    assert exported["insured_engine_number"]["/V"] == "ENG123456"
    assert exported["insured_chassis_number"]["/V"] == "CHS123456789"
    assert exported["estimated_repair_cost"]["/V"] == "12500"
    assert exported["accident_date"]["/V"] == "07102026"
    assert str(exported["cause_accident"]["/V"]) != "/Off"
