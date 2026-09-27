from pathlib import Path
from unittest.mock import patch

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
