from pathlib import Path

from backend.models import (
    ExtractedField,
    FieldEvidence,
    FormFieldDefinition,
    FormSchema,
    ProcessedDocument,
)
from backend.services.forms import FormSchemaService
from backend.services.validation import validate_form


def document_with_name(document_id: str, filename: str, name: str) -> ProcessedDocument:
    return ProcessedDocument(
        id=document_id,
        filename=filename,
        media_type="image/png",
        document_type="other_identity",
        extraction_method="test",
        raw_text=name,
        fields={
            "full_name": ExtractedField(
                key="full_name",
                value=name,
                normalized_value=name.title(),
                evidence=[
                    FieldEvidence(
                        document_id=document_id,
                        document_name=filename,
                        raw_value=name,
                        ocr_score=0.9,
                    )
                ],
            )
        },
    )


def name_schema() -> FormSchema:
    return FormSchema(
        id="test",
        title="Test",
        description="Test schema",
        fields=[
            FormFieldDefinition(
                key="full_name",
                label="Applicant name",
                section="Applicant",
                required=True,
            )
        ],
    )


def test_conflicting_values_preserve_all_options_and_sources():
    documents = [
        document_with_name("one", "aadhaar.png", "MANAV NEERAV PATHAK"),
        document_with_name("two", "licence.png", "PATHAK MANAV NEERAV"),
    ]
    form = FormSchemaService(Path("unused")).populate(name_schema(), documents)

    value = form.fields["full_name"]
    assert value.source == "conflict"
    assert value.value == ""
    assert [option.value for option in value.options] == [
        "Manav Neerav Pathak",
        "Pathak Manav Neerav",
    ]
    assert value.options[1].evidence[0].document_name == "licence.png"


def test_reordered_name_tokens_are_not_reported_as_identity_mismatch():
    documents = [
        document_with_name("one", "aadhaar.png", "MANAV NEERAV PATHAK"),
        document_with_name("two", "licence.png", "PATHAK MANAV NEERAV"),
    ]
    schema = name_schema()
    form = FormSchemaService(Path("unused")).populate(schema, documents)

    issues = validate_form(schema, form, documents)
    assert not any(issue.code == "name_mismatch" for issue in issues)


def test_genuinely_different_names_remain_flagged():
    documents = [
        document_with_name("one", "aadhaar.png", "MANAV NEERAV PATHAK"),
        document_with_name("two", "passport.png", "RAHUL SHARMA"),
    ]
    schema = name_schema()
    form = FormSchemaService(Path("unused")).populate(schema, documents)

    issues = validate_form(schema, form, documents)
    assert any(issue.code == "name_mismatch" for issue in issues)
