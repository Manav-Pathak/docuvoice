from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services.extraction import LocalDocumentExtractor
from backend.services.ocr import LocalOCRService, OCRResult


def test_local_rule_extraction_preserves_normalized_and_raw_values():
    text = """GOVERNMENT OF INDIA
Name: AARAV SHARMA
DOB: 14/08/1999
MALE
Address: 21 Lotus Residency, Mumbai, Maharashtra 400056
9999 9999 0019
Permanent Account Number ABCPK1234F"""
    document = LocalDocumentExtractor().process(
        OCRResult(text=text, fragments=[], method="test"),
        "identity.pdf",
        "application/pdf",
    )

    assert document.document_type == "pan"
    assert document.raw_text == text
    assert document.fields["full_name"].normalized_value == "Aarav Sharma"
    assert document.fields["aadhaar_number"].normalized_value == "999999990019"
    assert document.fields["pan_number"].normalized_value == "ABCPK1234F"
    assert document.fields["pincode"].normalized_value == "400056"


def test_paddle_cache_requires_both_complete_model_packages():
    service = LocalOCRService(Path("unused-test-cache"))
    with patch.object(Path, "is_file", return_value=False):
        assert not service.model_cached
    with patch.object(
        Path,
        "is_file",
        side_effect=[True, False],
    ):
        assert not service.model_cached
    with patch.object(Path, "is_file", return_value=True):
        assert service.model_cached


@pytest.mark.parametrize("text", [
    "Election Commission Of India\nDate Of Birth\nName: Election Commission Of India",
    "Insurance Policy\nICICI Lombard General Insurance Company Limited\nNibhaye Vaade",
    "GOVERNMENT OF INDIA\nDate Of Birth\nMALE",
])
def test_headers_and_unanchored_slogans_are_not_names(text):
    document = LocalDocumentExtractor().process(
        OCRResult(text=text, fragments=[], method="test"), "synthetic.txt", "text/plain",
    )
    assert "full_name" not in document.fields


def test_policy_uses_insured_labels_instead_of_insurer_and_issue_date():
    text = """Motor Insurance Policy
ICICI Lombard General Insurance Company Limited
Address: Insurer House, Mumbai 400025
Issued on: 15/10/2025
Name of Insured:
AARAV SHARMA
Insured Address: 21 Lotus Residency,
Mumbai, Maharashtra 400056
Policy Number: 123456789
"""
    document = LocalDocumentExtractor().process(
        OCRResult(text=text, fragments=[], method="test"), "synthetic.txt", "text/plain",
    )
    assert document.document_type == "insurance_policy"
    assert document.fields["full_name"].normalized_value == "Aarav Sharma"
    assert document.fields["address"].value == "21 Lotus Residency, Mumbai, Maharashtra 400056"
    assert document.fields["pincode"].value == "400056"
    assert "date_of_birth" not in document.fields


def test_aadhaar_name_before_dob_and_bounded_address():
    text = """GOVERNMENT OF INDIA
AARAV SHARMA
DOB: 14/08/1999
MALE
Address:
21 Lotus Residency, Mumbai 400056
9999 9999 0019
"""
    document = LocalDocumentExtractor().process(
        OCRResult(text=text, fragments=[], method="test"), "synthetic.txt", "text/plain",
    )
    assert document.fields["full_name"].normalized_value == "Aarav Sharma"
    assert document.fields["date_of_birth"].value == "14/08/1999"
    assert document.fields["address"].value == "21 Lotus Residency, Mumbai 400056"


def test_voter_name_label_does_not_select_authority_or_relative():
    text = """Election Commission Of India
Father's Name: RAHUL SHARMA
Name:
AARAV SHARMA
Date of Birth: 14/08/1999
"""
    document = LocalDocumentExtractor().process(
        OCRResult(text=text, fragments=[], method="test"), "synthetic.txt", "text/plain",
    )
    assert document.document_type == "voter_id"
    assert document.fields["full_name"].normalized_value == "Aarav Sharma"


def test_pan_cardholder_immediately_after_pan_is_not_fathers_name():
    text = """INCOME TAX DEPARTMENT
GOVERNMENT OF INDIA
Permanent Account Number
ABCPK1234F
AARAV SHARMA
RAHUL SHARMA
DOB: 14/08/1999
"""
    document = LocalDocumentExtractor().process(
        OCRResult(text=text, fragments=[], method="test"), "synthetic.txt", "text/plain",
    )
    assert document.fields["full_name"].normalized_value == "Aarav Sharma"
