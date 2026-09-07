"""
Public widget key generation (Stage 4 Phase A — Production Customer
Widget).

Unlike app/admin_keys.py's issued keys, a widget_key is NOT a secret: a
future phase's customer-facing widget will carry it in plain HTML on a
restaurant's public website (e.g. a <script data-restaurant-key="...">
attribute), and it will only ever resolve to that restaurant's PUBLIC
branding/config (see app/models.py:WidgetConfig) — never to anything
behind X-Admin-API-Key. There is nothing to protect by hashing it, so
unlike AdminUser.key_hash it is generated and stored in plaintext,
looked up directly, the same way WhatsAppNumber.phone_number_id is.

It still must be high-entropy and non-sequential: the internal integer
restaurant_id is never used as, or derivable from, this value, so one
restaurant can never guess or enumerate another's widget_key.
"""

import secrets

WIDGET_KEY_PREFIX = "wgt"


def generate_widget_key() -> str:
    """Returns a new opaque, non-sequential public widget key."""
    return f"{WIDGET_KEY_PREFIX}_{secrets.token_urlsafe(24)}"
