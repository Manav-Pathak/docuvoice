"""Load the cached OCR and speech models without allowing network access."""

import io
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont

from backend.config import get_settings
from backend.services.ocr import LocalOCRService
from backend.services.voice import FasterWhisperService


def build_test_image() -> bytes:
    image = Image.new("RGB", (1500, 520), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 68)
    except OSError:
        font = ImageFont.load_default(size=48)
    draw.multiline_text(
        (55, 45),
        "INCOME TAX DEPARTMENT\nName: AARAV SHARMA\nPAN: ABCPK1234F",
        fill="black",
        font=font,
        spacing=28,
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def main() -> None:
    settings = get_settings()
    ocr = LocalOCRService(settings.paddle_model_dir)
    if not ocr.model_cached:
        raise RuntimeError("PaddleOCR cache is incomplete.")
    result = ocr.extract(build_test_image(), "synthetic-pan.png", "image/png")
    if "ABCPK1234F" not in result.text.replace(" ", "").upper():
        raise RuntimeError(
            f"PaddleOCR loaded but did not read the test PAN: {result.text!r}"
        )
    print("PaddleOCR offline inference: OK")

    whisper = FasterWhisperService(settings.whisper_model_path)
    if not whisper.model_cached:
        raise RuntimeError("Faster-Whisper cache is incomplete.")
    from faster_whisper import WhisperModel

    WhisperModel(str(settings.whisper_model_path), device="cpu", compute_type="int8")
    print("Faster-Whisper offline model load after OCR: OK")


if __name__ == "__main__":
    main()
