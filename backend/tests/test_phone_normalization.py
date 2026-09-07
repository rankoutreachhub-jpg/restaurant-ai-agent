"""
app/phone.py: E.164 normalization/validation for WhatsApp customer phone
numbers (Stage 3 Step 6B).
"""

import pytest

from app.phone import InvalidPhoneNumberError, normalize_whatsapp_phone


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+447911123456", "+447911123456"),          # already E.164
        ("447911123456", "+447911123456"),            # Meta's bare-digit webhook shape
        ("0044 7911 123456", "+447911123456"),        # "00" international prefix, spaces
        ("+1 (415) 555-2671", "+14155552671"),        # formatting characters stripped
        ("0014155552671", "+14155552671"),            # "00" prefix, US number
    ],
)
def test_valid_numbers_normalize_to_e164(raw, expected):
    assert normalize_whatsapp_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "07911123456",       # UK trunk prefix, no country code — ambiguous, never guessed
        "123",               # too short to be a real E.164 number
        "+0123456789",       # leading zero after '+' is invalid in E.164
        "not-a-number",
        "+1234567890123456",  # 16 digits after '+' — exceeds E.164's max length
    ],
)
def test_invalid_numbers_are_rejected(raw):
    with pytest.raises(InvalidPhoneNumberError):
        normalize_whatsapp_phone(raw)


def test_normalization_never_guesses_a_country_code():
    """A national number with a trunk '0' and no country code is
    genuinely ambiguous (is it UK, is it some other country's trunk
    prefix?) — this must be rejected outright, never silently assigned
    a guessed country code."""
    with pytest.raises(InvalidPhoneNumberError):
        normalize_whatsapp_phone("07911123456")
