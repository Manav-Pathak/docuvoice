from io import BytesIO
from pathlib import Path

import pymupdf
from pypdf import PdfReader

from backend.models import OCRFragment
from backend.services.extraction import LocalDocumentExtractor
from backend.services.forms import FormSchemaService
from backend.services.ocr import OCRResult
from backend.services.pdf import PDFGenerationService


def admission(text, fragments=None):
    return LocalDocumentExtractor().process(OCRResult(text, fragments or [], "test"), "synthetic-admission.pdf", "application/pdf")


NOTE = """ADMISSION NOTE & PROVISIONAL COST ESTIMATE
Patient full name
Aarav Neel Sharma
DOB / age / sex
14 May 1988 / 38 years / Male
Residential address
21 Lotus Residency
City / state / pincode
Mumbai / Maharashtra / 400056
Patient contact
90000 00001
Policyholder name
Rahul Sharma
Policy / card number
HL-2026-000001 / CARD-000001
Admission date / time
09 October 2026 / 10:30 AM
Provisional diagnosis
Suspected appendicitis; assessment pending.
Planned treatment
Investigations and surgery if confirmed;
postoperative monitoring.
Estimated length of stay
3 days (anticipated discharge: 12 October 2026)
Admitting doctor
Dr. Kavya Rao - Department of Surgery
Hospital / provider ID
Lotus Demo Hospital / PROV-001
Hospital address
12 Main Road, Mumbai, Maharashtra 400056
TOTAL ESTIMATED MEDICAL EXPENSES
INR 78,000.00
Contact / relationship
Neel Sharma / Brother
Contact phone
90000 00002
Issue date / place
09 October 2026 / Mumbai"""


def test_combined_labels_and_month_name_dates_remain_distinct():
    doc = admission(NOTE)
    assert doc.document_type == "medical_admission"
    fields = doc.fields
    assert fields["policy_number"].value == "HL-2026-000001"
    assert fields["card_number"].value == "CARD-000001"
    assert fields["patient_full_name"].value == "Aarav Neel Sharma"
    assert fields["policyholder_full_name"].value == "Rahul Sharma"
    assert fields["patient_dob_day"].value == "14"
    assert fields["patient_dob_month"].value == "05"
    assert fields["patient_dob_year"].value == "1988"
    assert fields["patient_age_years"].value == "38"
    assert fields["admission_date"].value == "09/10/2026"
    assert fields["estimated_stay_days"].normalized_value == "3"
    assert fields["estimated_expenses"].normalized_value == "78000.00"
    assert fields["treatment_planned"].value.endswith("postoperative monitoring.")
    assert "Estimated" not in fields["treatment_planned"].value
    assert fields["provider_id"].value == "PROV-001"
    assert fields["provider_pincode"].value == "400056"
    assert "estimated_stay_days" in fields and "hospitalization_days" not in fields
    assert "claimed_amount" not in fields


def test_shuffled_table_rows_do_not_use_rate_as_total_or_extend_treatment():
    def f(text, x, y, width=100):
        return OCRFragment(text=text, page=1, bbox=[x, y, x + width, y + 10], region_id="page-1-text")
    fragments = [
        f("Admission note", 0, 0), f("Provisional diagnosis", 0, 20), f("Observation", 180, 20),
        f("Planned treatment", 0, 40), f("Investigations and", 180, 40), f("monitoring.", 180, 52),
        f("Estimated length of stay", 0, 65), f("3 days", 180, 65),
        f("Item", 0, 100), f("Basis", 200, 100), f("Amount (INR)", 350, 100),
        f("Room & nursing", 0, 120), f("3 days x 4,500", 200, 120), f("13,500.00", 400, 120),
        f("Consultations", 0, 140), f("Estimated", 200, 140), f("3,500.00", 400, 140),
        f("TOTAL ESTIMATED MEDICAL EXPENSES", 0, 160, 250), f("INR 17,000.00", 400, 160),
    ][::-1]
    fields = admission("\n".join(f.text for f in fragments), fragments).fields
    assert fields["treatment_planned"].value == "Investigations and monitoring."
    assert fields["estimated_expenses"].normalized_value == "17000.00"
    assert fields["medical_cost_breakdown"].value == "Room & nursing: 13500.00; Consultations: 3500.00"


def test_split_dob_and_patient_policyholder_names_export_to_separate_widgets():
    keys = ["policyholder_first_name", "policyholder_last_name", "admitted_person_first_name", "admitted_person_last_name",
            "admitted_dob_day", "admitted_dob_month", "admitted_dob_year", "card_number", "provider_name", "estimated_expenses"]
    pdf = pymupdf.open()
    page = pdf.new_page()
    for index, key in enumerate(keys):
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = key
        widget.rect = pymupdf.Rect(50, 30 + index * 30, 350, 55 + index * 30)
        page.add_widget(widget)
    content = pdf.tobytes()
    pdf.close()
    service = PDFGenerationService()
    schema = service.inspect_acroform(content, "synthetic-intimation.pdf").schema
    form = FormSchemaService(Path("unused")).populate(schema, [admission(NOTE)])
    fields = PdfReader(BytesIO(service.fill_acroform(content, schema, form))).get_fields()
    assert fields["policyholder_first_name"]["/V"] == "Rahul"
    assert fields["admitted_person_first_name"]["/V"] == "Aarav Neel"
    assert fields["admitted_dob_day"]["/V"] == "14"
    assert fields["admitted_dob_month"]["/V"] == "05"
    assert fields["admitted_dob_year"]["/V"] == "1988"
    assert fields["estimated_expenses"]["/V"] == "78000.00"


def test_estimate_conflicts_keep_each_document_source():
    left = admission(NOTE)
    right = admission(NOTE.replace("78,000.00", "80,000.00"))
    from backend.models import FormFieldDefinition, FormSchema
    schema = FormSchema(id="test", title="Test", description="Test", fields=[
        FormFieldDefinition(key="estimated_expenses", label="Estimated expenses", section="Medical"),
    ])
    value = FormSchemaService(Path("unused")).populate(schema, [left, right]).fields["estimated_expenses"]
    assert value.source == "conflict"
    assert {option.value for option in value.options} == {"78000.00", "80000.00"}
    assert {option.evidence[0].document_id for option in value.options} == {left.id, right.id}


def test_invalid_birth_date_is_not_replaced_by_issue_date():
    fields = admission(NOTE.replace("14 May 1988", "31 February 1988")).fields
    assert "patient_date_of_birth" not in fields
    assert fields["declaration_date"].value == "09/10/2026"


def test_short_medical_rows_preserve_complete_treatment_and_reduce_font():
    pdf = pymupdf.open()
    page = pdf.new_page()
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "treatment_planned"
    widget.field_flags = pymupdf.PDF_TX_FIELD_IS_MULTILINE
    widget.text_fontsize = 9
    widget.rect = pymupdf.Rect(50, 50, 240, 70)
    page.add_widget(widget)
    content = pdf.tobytes()
    pdf.close()
    service = PDFGenerationService()
    schema = service.inspect_acroform(content, "medical-row.pdf").schema
    form = FormSchemaService(Path("unused")).populate(schema, [admission(NOTE)])
    result = pymupdf.open(stream=service.fill_acroform(content, schema, form), filetype="pdf")
    filled = next(result[0].widgets())
    assert filled.field_value.endswith("postoperative monitoring.")
    assert filled.text_fontsize < 9
    assert "monitoring." in result[0].get_text()
    result.close()
