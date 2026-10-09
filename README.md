# DocuVoice

DocuVoice is an offline-first React and FastAPI application that extracts identity data, maps it into an uploaded AcroForm insurance PDF, supports deterministic validation and voice corrections, and exports a filled copy of the original form.

The default workflow does not need Gemini, a CDN, a hosted database, embeddings, RAG, or a local LLM. Optional online services live behind replaceable interfaces and cannot erase the backend-owned form state when unavailable.

## Current offline workflow

1. Upload a digital PDF or an image/scanned PDF.
2. Extract selectable PDF text directly and run PaddleOCR only on embedded raster images or uploaded images. Mixed PDFs combine both sources; whole PDF pages are never rendered for OCR.
3. Classify Aadhaar, PAN, voter ID, driving licences, and insurance-policy text with local rules while preserving raw text, normalized values, source documents, bounding boxes, and OCR scores. Match labels to nearby values within each OCR image region, so shuffled multi-column text does not mix labels from different images. Extract motor-policy identifiers, licence details, labelled accident notes, and card addresses. Unsupported or ambiguous layouts can leave values missing for manual review.
4. Upload a fillable AcroForm PDF. Its field names, tooltips, types, choices, and required flags are inspected locally with pypdf.
5. Map only non-conflicting identity values into semantically matching form fields.
6. Review required fields, PAN format, Aadhaar Verhoeff checksum, dates, and cross-document name/value consistency.
7. Correct fields with typed commands or cached Faster-Whisper speech recognition.
8. Hear local operating-system speech responses and preview/download the completed original PDF layout.

The **Use fabricated demo identity** action exercises the full mapping, validation, correction, and PDF pipeline without a real document or OCR model.

For motor claims, insured names come from policy labels and driver names from
driving licences or explicit driver labels. Different extracted addresses remain
source choices. Supported licence details include DL number, Valid Till/expiry,
issuing authority, DOB, LMV and motorcycle classes. `Address`, `Add`, and
`Communication Address` blocks can supply address, pincode, and recognised city
and state names. Small OCR label errors such as `Chasis No`, `AddFlat`, `DOB04`,
and `PIN400067` are handled without guessing identifier characters.

An accident is not inferred from a policy. A supporting note can use labels such
as `Accident Date`, `Accident Time: 09:30 PM`, `Accident Place`, `Cause of Damage`,
`Number of Occupants`, `Occupant Names`, `Estimated Repair Cost`, `Claimed Amount`,
`Accident Description`, `Driver Relationship`, `Under Influence`, and declaration
date/place. Hospital and third-party details require manual input. Full addresses
and descriptions map to the first corresponding PDF line; second-line widgets
are not automatically duplicated. Multiple applicable licence classes remain
choices for the user rather than selecting one automatically.

## Run locally

Prerequisites: Python 3.11 (selected by `.python-version`), [uv](https://docs.astral.sh/uv/), and Node.js.

```powershell
uv sync
npm install
```

Start the backend and frontend in separate terminals:

```powershell
uv run python main.py
```

```powershell
npm run dev
```

Open `http://127.0.0.1:5173`. The API and OpenAPI page are at `http://127.0.0.1:8000` and `http://127.0.0.1:8000/docs`.

## Prepare models before going offline

Model download is never performed at application startup. While connected to the internet, run this once:

```powershell
uv run python scripts/cache_models.py
```

If another virtual environment is already activated, either deactivate it first or use
`uv run --active python scripts/cache_models.py`. The warning that an active environment
such as `.venv312` does not match the project `.venv` only means uv selected the project
environment; it is not a model-download failure.

It stores PaddleOCR and Faster-Whisper weights below `.models/`, which is intentionally git-ignored. Confirm `/api/health` reports both `paddle_model_cached` and `whisper_model_cached` as `true`, then disconnect Wi-Fi and test an image upload plus microphone command.

To force both cached model packages to load with network access disabled and run a
synthetic OCR inference check:

```powershell
uv run python scripts/verify_offline_models.py
```

Text-only digital PDFs and the fabricated demo work even when OCR weights are absent. PDFs containing images require cached OCR weights, including mixed text/image documents. Typed voice-style commands remain available when Faster-Whisper weights are absent.

## Optional online mode

Copy `.env.example` to `.env`, provide a backend-only Gemini key, restart the API, and explicitly switch mode in the interface. The current phase exposes the online service boundary but deliberately keeps the production extraction and mapping pipeline local. A failed online-mode request returns an error without changing the current session or form.

## Project structure

```text
backend/
  app.py                 FastAPI routes and WebSocket command channel
  models.py              Pydantic contracts and provenance models
  schemas/               Versioned local insurance form definitions
  services/
    ocr.py               Digital PDF extraction and lazy PaddleOCR adapter
    extraction.py        Deterministic identity-document rules
    forms.py             Local schema loading and conflict-safe mapping
    validation.py        PAN, Aadhaar, date, required, and cross-doc checks
    voice.py             Local intents, slots, and Faster-Whisper adapter
    pdf.py               AcroForm inspection and original-template filling
    online.py            Optional AI service interface
src/                     Responsive React review application
scripts/cache_models.py  Explicit development-time model download
tests/                   Offline unit and API flow tests
```

## Verification

```powershell
uv run pytest -q
uv run ruff check backend tests scripts main.py
npm run lint
npm run build
```

## Privacy and scope

- The demo data is fabricated. Do not use production identity documents in the college demonstration.
- API keys remain backend-only and are not part of frontend bundles.
- Uploaded identity and form bytes are processed in memory; this MVP does not persist or log their contents.
- OCR scores are shown as recognition indicators, not calibrated probabilities.
- Conflicting document values are left unresolved for the user instead of being selected automatically.
- Sessions are intentionally in-memory for the single-machine demo and reset when the backend restarts.

## AcroForm scope

The current form-upload workflow intentionally accepts only PDFs with AcroForm fields. Flat or scanned blank forms return a clear validation error and leave the current session unchanged. Existing vendor field names are retained for export while a separate semantic key is used for document mapping, so fields such as `insured_pan` can receive extracted `pan_number` data without changing the original PDF structure.
