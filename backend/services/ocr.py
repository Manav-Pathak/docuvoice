from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
from PIL import Image

from backend.models import OCRFragment


class OCRUnavailableError(RuntimeError):
    pass


@dataclass
class OCRResult:
    text: str
    fragments: list[OCRFragment]
    method: str


class LocalOCRService:
    """Reads PDF text directly and runs PaddleOCR only on raster images."""

    detection_model_name = "PP-OCRv4_mobile_det"
    recognition_model_name = "en_PP-OCRv4_mobile_rec"

    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self._engine: Any | None = None

    @property
    def detection_model_dir(self) -> Path:
        return (
            self.model_dir / ".runtime" / "official_models" / self.detection_model_name
        )

    @property
    def recognition_model_dir(self) -> Path:
        return (
            self.model_dir
            / ".runtime"
            / "official_models"
            / self.recognition_model_name
        )

    @property
    def model_cached(self) -> bool:
        return (self.detection_model_dir / "inference.yml").is_file() and (
            self.recognition_model_dir / "inference.yml"
        ).is_file()

    def extract(self, content: bytes, filename: str, media_type: str) -> OCRResult:
        is_pdf = media_type == "application/pdf" or filename.lower().endswith(".pdf")
        if is_pdf:
            return self._extract_pdf(content)
        return self._extract_image(content, page=1)

    def _extract_pdf(self, content: bytes) -> OCRResult:
        try:
            document = pymupdf.open(stream=content, filetype="pdf")
        except Exception as exc:
            raise ValueError("The uploaded PDF could not be opened.") from exc

        fragments: list[OCRFragment] = []
        has_digital_text = False
        has_images = False
        with document:
            for page_number, page in enumerate(document, start=1):
                for block_index, block in enumerate(page.get_text("dict")["blocks"]):
                    if block["type"] == 1:
                        has_images = True
                        result = self._extract_image(block["image"], page_number)
                        # PDF image transforms map the unit square to the page.
                        # OCR boxes are measured in pixels of the extracted image.
                        transform = pymupdf.Matrix(
                            1 / block["width"], 1 / block["height"]
                        ) * pymupdf.Matrix(*block["transform"])
                        for fragment in result.fragments:
                            bbox = (
                                list(pymupdf.Rect(fragment.bbox) * transform)
                                if fragment.bbox is not None
                                else list(block["bbox"])
                            )
                            fragments.append(fragment.model_copy(update={
                                "bbox": bbox,
                                "region_id": f"page-{page_number}-image-{block_index}",
                            }))
                        continue
                    for line in block.get("lines", []):
                        text = " ".join(
                            "".join(span["text"] for span in line["spans"]).split()
                        )
                        if text:
                            has_digital_text = True
                            fragments.append(
                                OCRFragment(
                                    text=text,
                                    confidence=None,
                                    page=page_number,
                                    bbox=[float(value) for value in line["bbox"]],
                                    region_id=f"page-{page_number}-text",
                                )
                            )
        fragments.sort(key=lambda fragment: (
            fragment.page, (fragment.bbox or [0, 0])[1], (fragment.bbox or [0, 0])[0],
        ))
        method = (
            "digital_pdf_text+paddleocr"
            if has_digital_text and has_images
            else "paddleocr" if has_images else "digital_pdf_text"
        )
        return OCRResult(
            "\n".join(fragment.text for fragment in fragments), fragments, method,
        )

    def _build_engine(self):
        if not self.model_cached:
            raise OCRUnavailableError(
                "PaddleOCR weights are not cached. Run scripts/cache_models.py while online "
                "before using image or scanned-PDF OCR."
            )
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(self.model_dir / ".runtime"))
        from paddleocr import PaddleOCR

        return PaddleOCR(
            text_detection_model_name=self.detection_model_name,
            text_detection_model_dir=str(self.detection_model_dir),
            text_recognition_model_name=self.recognition_model_name,
            text_recognition_model_dir=str(self.recognition_model_dir),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    def _extract_image(self, content: bytes, page: int) -> OCRResult:
        try:
            image = Image.open(io.BytesIO(content)).convert("RGB")
        except Exception as exc:
            raise ValueError("The uploaded image could not be decoded.") from exc

        if self._engine is None:
            self._engine = self._build_engine()

        predictions = list(self._engine.predict(np.asarray(image)))
        fragments: list[OCRFragment] = []
        for prediction in predictions:
            payload = getattr(prediction, "json", prediction)
            if callable(payload):
                payload = payload()
            if isinstance(payload, dict) and "res" in payload:
                payload = payload["res"]
            if not isinstance(payload, dict):
                continue
            texts = payload.get("rec_texts", [])
            scores = payload.get("rec_scores", [])
            boxes = payload.get("rec_boxes") or payload.get("dt_polys") or []
            for index, text in enumerate(texts):
                bbox = None
                if index < len(boxes):
                    raw_box = boxes[index]
                    if hasattr(raw_box, "tolist"):
                        raw_box = raw_box.tolist()
                    if raw_box and isinstance(raw_box[0], list):
                        xs = [point[0] for point in raw_box]
                        ys = [point[1] for point in raw_box]
                        bbox = [
                            float(min(xs)),
                            float(min(ys)),
                            float(max(xs)),
                            float(max(ys)),
                        ]
                    elif len(raw_box) >= 4:
                        bbox = [float(value) for value in raw_box[:4]]
                fragments.append(
                    OCRFragment(
                        text=str(text),
                        confidence=float(scores[index])
                        if index < len(scores)
                        else None,
                        page=page,
                        bbox=bbox,
                    )
                )
        return OCRResult(
            "\n".join(fragment.text for fragment in fragments), fragments, "paddleocr"
        )
