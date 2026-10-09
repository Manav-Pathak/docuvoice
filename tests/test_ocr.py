from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest
from PIL import Image

from backend.models import OCRFragment
from backend.services.ocr import LocalOCRService, OCRResult


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (100, 50), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("text", ["", "Name: Aarav", "Selectable text " * 3])
def test_text_only_pdf_never_uses_ocr_or_page_rendering(text):
    with pymupdf.open() as document:
        page = document.new_page()
        if text:
            page.insert_text((50, 50), text)
        content = document.tobytes()
    service = LocalOCRService(Path("unused-test-cache"))
    with (
        patch.object(service, "_extract_image") as ocr,
        patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("Page rendered")),
    ):
        result = service.extract(content, "synthetic.pdf", "application/pdf")
    ocr.assert_not_called()
    assert result.method == "digital_pdf_text"
    assert result.text == text.strip()


def test_mixed_pdf_reads_text_and_ocrs_only_images_with_page_coordinates():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 50), "Long selectable header that used to skip image OCR")
        page.insert_image(pymupdf.Rect(40, 100, 240, 200), stream=image_bytes())
        second = document.new_page()
        second.insert_image(pymupdf.Rect(40, 100, 240, 200), stream=image_bytes())
        content = document.tobytes()
    service = LocalOCRService(Path("unused-test-cache"))

    def recognize(content, page):
        with Image.open(BytesIO(content)) as image:
            assert image.size == (100, 50)  # Original image, not a page screenshot.
        return OCRResult(
            "Name: Aarav Sharma",
            [OCRFragment(text="Name: Aarav Sharma", confidence=0.9,
                         page=page, bbox=[10, 5, 80, 30])],
            "paddleocr",
        )

    with (
        patch.object(service, "_extract_image", side_effect=recognize) as ocr,
        patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("Page rendered")),
    ):
        result = service.extract(content, "synthetic.pdf", "application/pdf")
    assert ocr.call_count == 2
    assert result.method == "digital_pdf_text+paddleocr"
    assert "Long selectable header" in result.text
    recognized = [fragment for fragment in result.fragments if fragment.confidence is not None]
    assert [fragment.page for fragment in recognized] == [1, 2]
    for fragment in recognized:
        assert fragment.bbox == pytest.approx([60, 110, 200, 160])


def test_scanned_pdf_extracts_image_without_rendering_page():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_image(page.rect, stream=image_bytes())
        content = document.tobytes()
    service = LocalOCRService(Path("unused-test-cache"))
    recognized = OCRResult("Scanned name", [OCRFragment(text="Scanned name")], "paddleocr")
    with (
        patch.object(service, "_extract_image", return_value=recognized) as ocr,
        patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("Page rendered")),
    ):
        result = service.extract(content, "synthetic.pdf", "application/pdf")
    ocr.assert_called_once()
    assert result.method == "paddleocr"
    assert result.text == "Scanned name"
    assert result.fragments[0].bbox is not None
