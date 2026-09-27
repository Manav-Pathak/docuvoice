from backend.services.validation import is_valid_aadhaar, is_valid_date, is_valid_pan


def test_pan_structure():
    assert is_valid_pan("ABCPK1234F")
    assert not is_valid_pan("ABC1234F")


def test_aadhaar_verhoeff_checksum():
    assert is_valid_aadhaar("9999 9999 0019")
    assert not is_valid_aadhaar("9999 9999 0018")
    assert not is_valid_aadhaar("1234 1234 1234")


def test_date_validation_is_deterministic():
    assert is_valid_date("14/08/1999")
    assert not is_valid_date("31/02/2020")
