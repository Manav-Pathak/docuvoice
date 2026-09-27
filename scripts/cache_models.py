"""Cache all model weights while internet access is available.

Run this explicitly during setup. The application never invokes this script.
"""

import os
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / ".models"
PADDLE_OCR_VERSION = "PP-OCRv4"
PADDLE_DETECTION_MODEL = "PP-OCRv4_mobile_det"
PADDLE_RECOGNITION_MODEL = "en_PP-OCRv4_mobile_rec"


def cache_whisper() -> None:
    target = MODEL_ROOT / "faster-whisper-small"
    snapshot_download(repo_id="Systran/faster-whisper-small", local_dir=target)
    print(f"Faster-Whisper cached at {target}")


def cache_paddle() -> None:
    target = MODEL_ROOT / "paddleocr"
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(target / ".runtime"))
    from paddleocr import PaddleOCR

    PaddleOCR(
        lang="en",
        ocr_version=PADDLE_OCR_VERSION,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    detection = target / ".runtime" / "official_models" / PADDLE_DETECTION_MODEL
    recognition = target / ".runtime" / "official_models" / PADDLE_RECOGNITION_MODEL
    required = [detection / "inference.yml", recognition / "inference.yml"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "PaddleOCR initialization finished without caching the expected files: "
            + ", ".join(missing)
        )
    print(f"PaddleOCR cached at {target}")


if __name__ == "__main__":
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    cache_whisper()
    cache_paddle()
    print("Model cache is ready. The application can now run with Wi-Fi disabled.")
