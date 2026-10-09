from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from pypdf import PdfReader

from backend.models import FormState, FormValue
from backend.services.pdf import AcroFormError, PDFGenerationService

TEMPLATE = Path(__file__).resolve().parents[1] / "backend/forms/ClaimForm-TwoWheeler-247542505210 (1).pdf"


def claim_form(values):
    service = PDFGenerationService()
    content = TEMPLATE.read_bytes()
    schema = service.inspect_acroform(content, TEMPLATE.name).schema
    form = FormState(schema_id=schema.id, title=schema.title,
                     fields={key: FormValue(value=value) for key, value in values.items()})
    return service, content, schema, form


def test_claim_export_fits_full_vehicle_and_continues_address_with_valid_appearances():
    address = "Flat No.101, Punit Ganga C.H.S, Plot No.26, Gokhale Road"
    service, content, schema, form = claim_form({
        "insured_address_line_1": address,
        "insured_vehicle_number": "MH-02-BH-0712",
    })
    output = service.fill_acroform(content, schema, form)
    fields = PdfReader(BytesIO(output)).get_fields()
    assert fields["insured_vehicle_number"]["/V"] == "MH02BH0712"
    assert fields["insured_address_line_1"]["/V"] == "Flat No101, Punit Ganga CHS, Plot No26,"
    assert fields["insured_address_line_2"]["/V"] == "Gokhale Road"
    assert form.fields["insured_address_line_1"].value == address
    assert next(f for f in schema.fields if f.key == "insured_address_line_1").max_length is None
    assert next(f for f in schema.fields if f.key == "insured_vehicle_number").max_length is None
    with pymupdf.open(stream=output, filetype="pdf") as document:
        for page in document:
            for widget in page.widgets() or []:
                if widget.field_name in {"insured_address_line_1", "insured_address_line_2", "insured_vehicle_number"}:
                    assert widget.field_value == fields[widget.field_name]["/V"]
                    assert document.xref_get_key(widget.xref, "AP/N")[0] == "xref"


def test_explicit_second_line_is_preserved_and_short_address_has_no_duplicate():
    service, content, schema, form = claim_form({
        "insured_address_line_1": "12-A Main Rd.",
        "insured_address_line_2": "Near station",
    })
    fields = PdfReader(BytesIO(service.fill_acroform(content, schema, form))).get_fields()
    assert fields["insured_address_line_1"]["/V"] == "12A Main Rd"
    assert fields["insured_address_line_2"]["/V"] == "Near station"


@pytest.mark.parametrize("values", [
    {"insured_address_line_1": "A" * 69},
    {"insured_vehicle_number": "MH02BH071234"},
    {"insured_address_line_1": "A" * 47, "insured_address_line_2": "Existing text"},
])
def test_overflow_is_reported_instead_of_exporting_clipped_text(values):
    service, content, schema, form = claim_form(values)
    with pytest.raises(AcroFormError, match="exceeds"):
        service.fill_acroform(content, schema, form)
