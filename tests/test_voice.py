from pathlib import Path

from backend.services.forms import FormSchemaService
from backend.services.voice import LocalIntentParser

SCHEMAS = FormSchemaService(Path("backend/schemas"))


def test_update_command_and_spoken_digits():
    schema = SCHEMAS.get("personal_accident_claim")
    result = LocalIntentParser().parse(
        "Change my pincode to four zero zero zero five six.", schema
    )
    assert result == {"intent": "update_field", "field": "pincode", "value": "400056"}


def test_missing_and_navigation_commands():
    schema = SCHEMAS.get("personal_accident_claim")
    parser = LocalIntentParser()
    assert (
        parser.parse("Which fields are still missing?", schema)["intent"]
        == "get_missing_fields"
    )
    assert parser.parse("Go to the address section", schema) == {
        "intent": "navigate_to_field",
        "field": "address",
    }
