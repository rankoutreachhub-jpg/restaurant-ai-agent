"""
Phone number normalization/validation (Stage 3 Step 6B — WhatsApp
integration).

WhatsApp customer phone numbers must be compared and stored consistently
(see Conversation.external_id in app/models.py), so every number is
normalized to E.164 ("+" followed by 8-15 digits, first digit non-zero)
through this one function before it is ever used as a lookup key.

Meta's webhook payloads give the sender's number as plain digits with no
leading "+" (e.g. "447911123456") even though it IS already E.164 — this
is a well-known, unambiguous shape, so a bare digit string is normalized
by adding the "+". Likewise "00" is a standard, unambiguous international
dialing prefix, so it is translated to "+". Anything else that doesn't
already look like a complete E.164 number (e.g. a national number with no
country code, such as a UK number starting with a trunk "0") is REJECTED
rather than guessed at — this module never invents or assumes a country
code.
"""

import re

# Formatting characters that are unambiguous to strip: spaces, hyphens,
# and parentheses. Nothing else is touched.
_STRIP_CHARS = str.maketrans("", "", " -()")

# "+" followed by a non-zero digit and 6-14 more digits: 7-15 digits
# total, matching E.164's max length and its "no leading zero" rule.
_E164_PATTERN = re.compile(r"^\+[1-9]\d{6,14}$")


class InvalidPhoneNumberError(ValueError):
    """Raised when a phone number can't be normalized to E.164 without
    guessing at missing information (e.g. a country code)."""


def normalize_whatsapp_phone(raw: str) -> str:
    """Returns `raw` normalized to E.164, or raises InvalidPhoneNumberError.

    Never silently reshapes an ambiguous number — only unambiguous,
    format-only transformations are applied (stripping spaces/hyphens/
    parentheses, translating a leading "00" to "+", adding a "+" to a
    bare digit string that is already a complete E.164 number without
    it, exactly the shape Meta's webhook payloads use)."""
    if raw is None:
        raise InvalidPhoneNumberError("Phone number is required")

    value = raw.strip().translate(_STRIP_CHARS)
    if not value:
        raise InvalidPhoneNumberError("Phone number is required")

    if value.startswith("00"):
        value = "+" + value[2:]
    elif not value.startswith("+"):
        if not value.isdigit():
            raise InvalidPhoneNumberError(f"Invalid phone number: {raw!r}")
        value = "+" + value

    if not _E164_PATTERN.match(value):
        raise InvalidPhoneNumberError(f"Invalid phone number: {raw!r}")

    return value
