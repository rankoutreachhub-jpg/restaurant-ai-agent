"""
Pydantic schemas define the shape of data going in and out of the API.
FastAPI uses these to validate requests and to auto-generate the
interactive API docs at /docs.
"""

import re
from datetime import date as date_type, datetime, time as time_type
from urllib.parse import urlparse

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import List, Literal, Optional


CHAT_MESSAGE_MAX_LENGTH = 2000
CHAT_HISTORY_MAX_TURNS = 40


class ChatMessage(BaseModel):
    # Restricted to the two real conversation roles so a client can't
    # inject a history entry with, e.g., role="system" that gets passed
    # straight into the Gemini call in llm.py.
    role: Literal["user", "assistant"]
    content: str = Field(
        ...,
        max_length=CHAT_MESSAGE_MAX_LENGTH,
        description="A single past turn's text",
    )


class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=CHAT_MESSAGE_MAX_LENGTH,
        description="The customer's message",
    )
    history: Optional[List[ChatMessage]] = Field(
        default=[],
        max_length=CHAT_HISTORY_MAX_TURNS,
        description="Previous turns in this conversation, oldest first. "
                     "Capped so a client can't force unbounded, costly "
                     "context into every Gemini call.",
    )
    restaurant_id: int = Field(
        default=1,
        description="Which restaurant this chat belongs to"
    )
    conversation_token: Optional[str] = Field(
        default=None,
        description="Opaque token from a previous X-Conversation-Token response "
                     "header, to resume that conversation's persisted history. "
                     "Omit to start a new conversation. Unknown or cross-restaurant "
                     "tokens are treated exactly like no token — a new conversation "
                     "starts silently, never an error.",
    )


class ChatResponse(BaseModel):
    reply: str


class RestaurantUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    map_link: Optional[str] = None
    parking_notes: Optional[str] = None
    seating_capacity: Optional[int] = Field(default=None, ge=1)


class MenuItemCreate(BaseModel):
    category: str
    name: str
    description: Optional[str] = None
    price: float
    dietary_tags: Optional[str] = None


class MenuItemUpdate(BaseModel):
    category: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    dietary_tags: Optional[str] = None


class OpeningHoursUpdate(BaseModel):
    day_of_week: Optional[str] = None
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    is_closed: Optional[bool] = None


# --- FAQs (Stage 3 Step 4: restaurant onboarding completeness) ---

FAQ_QUESTION_MAX_LENGTH = 500
FAQ_ANSWER_MAX_LENGTH = 2000


class FAQCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=FAQ_QUESTION_MAX_LENGTH)
    answer: str = Field(..., min_length=1, max_length=FAQ_ANSWER_MAX_LENGTH)


class FAQUpdate(BaseModel):
    question: Optional[str] = Field(default=None, min_length=1, max_length=FAQ_QUESTION_MAX_LENGTH)
    answer: Optional[str] = Field(default=None, min_length=1, max_length=FAQ_ANSWER_MAX_LENGTH)


# --- Opening-hours creation (Stage 3 Step 4) ---
# A newly onboarded restaurant has no OpeningHours rows at all (only
# PATCH-to-update existed before this stage). This lets one or all
# seven days be created through the same endpoint, with real HH:MM
# format validation — a gap that existed even in OpeningHoursUpdate
# above, which this deliberately does not retroactively change.

DayOfWeek = Literal[
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]


class OpeningHoursCreateItem(BaseModel):
    day_of_week: DayOfWeek
    open_time: Optional[str] = Field(default=None, description="24-hour \"HH:MM\", e.g. \"12:00\"")
    close_time: Optional[str] = Field(default=None, description="24-hour \"HH:MM\", e.g. \"22:00\"")
    is_closed: bool = False

    @field_validator("open_time", "close_time")
    @classmethod
    def _validate_time_format(cls, v):
        if v is None:
            return v
        # Same "HH:MM" shape app/booking.py's _parse_hhmm() expects when
        # it later reads these values back out for availability checks —
        # validated here so bad input is rejected at creation (422)
        # instead of surfacing as an unhandled error during a booking.
        try:
            datetime.strptime(v, "%H:%M")
        except ValueError:
            raise ValueError('must be in 24-hour "HH:MM" format, e.g. "12:00"')
        return v

    @model_validator(mode="after")
    def _require_times_unless_closed(self):
        if not self.is_closed and (self.open_time is None or self.close_time is None):
            raise ValueError("open_time and close_time are required unless is_closed is true")
        return self


def _validate_no_duplicate_days_in_request(items: List[OpeningHoursCreateItem]) -> List[OpeningHoursCreateItem]:
    seen = set()
    for item in items:
        if item.day_of_week in seen:
            raise ValueError(f"Duplicate day_of_week in request: {item.day_of_week}")
        seen.add(item.day_of_week)
    return items


class OpeningHoursCreate(BaseModel):
    # A plain list would also work as the request body, but wrapping it
    # lets us validate cross-item constraints (no day repeated within
    # the same request) with a single model-level validator.
    days: List[OpeningHoursCreateItem] = Field(..., min_length=1, max_length=7)

    @field_validator("days")
    @classmethod
    def _check_no_duplicates(cls, v):
        return _validate_no_duplicate_days_in_request(v)


# --- Bookings ---

BOOKING_NAME_MAX_LENGTH = 200
BOOKING_CONTACT_MAX_LENGTH = 30
BOOKING_NOTES_MAX_LENGTH = 500
BOOKING_MAX_PARTY_SIZE = 20
# How far ahead a booking can be made — a deliberate cap, same rationale
# as the /chat input limits: bounds obviously-bogus input (e.g. a date
# decades out) rather than any real business constraint.
BOOKING_MAX_ADVANCE_DAYS = 365

BookingStatus = Literal["confirmed", "cancelled"]


def _validate_booking_date(value: Optional[date_type]) -> Optional[date_type]:
    if value is None:
        return value
    today = date_type.today()
    if value < today:
        raise ValueError("booking_date cannot be in the past")
    if (value - today).days > BOOKING_MAX_ADVANCE_DAYS:
        raise ValueError(f"booking_date cannot be more than {BOOKING_MAX_ADVANCE_DAYS} days in the future")
    return value


class BookingCreate(BaseModel):
    customer_name: str = Field(..., min_length=1, max_length=BOOKING_NAME_MAX_LENGTH)
    phone: str = Field(..., min_length=1, max_length=BOOKING_CONTACT_MAX_LENGTH)
    email: EmailStr
    booking_date: date_type
    booking_time: time_type
    party_size: int = Field(..., ge=1, le=BOOKING_MAX_PARTY_SIZE)
    notes: Optional[str] = Field(default=None, max_length=BOOKING_NOTES_MAX_LENGTH)

    @field_validator("booking_date")
    @classmethod
    def _check_booking_date(cls, v):
        return _validate_booking_date(v)


class BookingUpdate(BaseModel):
    customer_name: Optional[str] = Field(default=None, min_length=1, max_length=BOOKING_NAME_MAX_LENGTH)
    phone: Optional[str] = Field(default=None, min_length=1, max_length=BOOKING_CONTACT_MAX_LENGTH)
    email: Optional[EmailStr] = None
    booking_date: Optional[date_type] = None
    booking_time: Optional[time_type] = None
    party_size: Optional[int] = Field(default=None, ge=1, le=BOOKING_MAX_PARTY_SIZE)
    status: Optional[BookingStatus] = None
    notes: Optional[str] = Field(default=None, max_length=BOOKING_NOTES_MAX_LENGTH)

    @field_validator("booking_date")
    @classmethod
    def _check_booking_date(cls, v):
        return _validate_booking_date(v)


class BookingOut(BaseModel):
    id: int
    restaurant_id: int
    customer_name: str
    phone: str
    email: str
    booking_date: date_type
    booking_time: time_type
    party_size: int
    status: str
    notes: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Platform admin (Stage 3 Step 3: multi-tenant authorization) ---
# Superadmin-only endpoints for onboarding restaurants and issuing/
# managing restaurant-scoped admin keys. See app/routers/platform_admin.py.

class RestaurantCreate(BaseModel):
    name: str = Field(..., min_length=1)
    address: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=1)
    email: EmailStr
    map_link: Optional[str] = None
    parking_notes: Optional[str] = None
    seating_capacity: int = Field(default=40, ge=1)


class AdminUserCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=200)
    restaurant_ids: List[int] = Field(..., min_length=1)


class AdminUserOut(BaseModel):
    id: int
    label: str
    is_active: bool
    created_at: datetime
    restaurant_ids: List[int]

    model_config = {"from_attributes": True}


class AdminUserCreateOut(AdminUserOut):
    # The plaintext key is only ever present in this creation/rotation
    # response — it is never retrievable again afterwards (only its
    # hash is stored; see app/admin_keys.py).
    api_key: str


class AdminUserUpdate(BaseModel):
    is_active: Optional[bool] = None
    label: Optional[str] = Field(default=None, min_length=1, max_length=200)


# --- Admin identity (Stage 3 Step 6A: dashboard) ---

class AdminMeOut(BaseModel):
    is_superadmin: bool
    # None means "every restaurant" (superadmin). For a scoped admin,
    # the exact set require_restaurant_access already enforces.
    restaurant_ids: Optional[List[int]] = None


class RestaurantOut(BaseModel):
    id: int
    name: str
    address: str
    phone: str
    email: str
    map_link: Optional[str] = None
    parking_notes: Optional[str] = None
    seating_capacity: int

    model_config = {"from_attributes": True}


# --- WhatsApp number mapping (Stage 3 Step 6B) ---
# Superadmin-only, platform-admin management of the one-per-restaurant
# WhatsApp phone_number_id mapping — see app/models.py:WhatsAppNumber.

class WhatsAppNumberCreate(BaseModel):
    phone_number_id: str = Field(..., min_length=1)
    display_phone_number: str = Field(..., min_length=1)


class WhatsAppNumberOut(BaseModel):
    id: int
    restaurant_id: int
    phone_number_id: str
    display_phone_number: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Widget config (Stage 4 Phase A: Production Customer Widget) ---
# Restaurant-scoped admin management of the one-per-restaurant public
# widget branding/config — see app/models.py:WidgetConfig. widget_key
# itself is never client-suppliable; it's generated server-side (see
# app/widget_keys.py) and only ever returned, never accepted as input.

WIDGET_WELCOME_MESSAGE_MAX_LENGTH = 500
WIDGET_LANGUAGE_MAX_LENGTH = 20
WIDGET_LOGO_URL_MAX_LENGTH = 2000

# Exactly "#" + 6 hex digits (e.g. "#7a2e2e") — no 3-digit shorthand, no
# named colors, nothing a browser would need to further interpret.
_HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


class WidgetConfigUpdate(BaseModel):
    """
    Used for both creating and updating a restaurant's widget config
    (the POST endpoint upserts — see app/routers/admin.py) — every field
    is optional so a partial update only touches what's provided
    (exclude_unset=True at the call site), matching this project's
    existing RestaurantUpdate/AdminUserUpdate convention.
    """
    welcome_message: Optional[str] = Field(default=None, max_length=WIDGET_WELCOME_MESSAGE_MAX_LENGTH)
    primary_language: Optional[str] = Field(default=None, min_length=1, max_length=WIDGET_LANGUAGE_MAX_LENGTH)
    logo_url: Optional[str] = Field(default=None, max_length=WIDGET_LOGO_URL_MAX_LENGTH)
    accent_color: Optional[str] = None
    booking_enabled: Optional[bool] = None
    is_active: Optional[bool] = None

    @field_validator("welcome_message", "primary_language", "logo_url")
    @classmethod
    def _blank_becomes_none(cls, v):
        # An empty string means "clear this field" rather than "set it to
        # the empty string" — treated as None so it falls back to the
        # model's default/blank display, not a visibly-empty value.
        if v is not None and v.strip() == "":
            return None
        return v

    @field_validator("logo_url")
    @classmethod
    def _validate_logo_url(cls, v):
        if v is None:
            return None
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError('logo_url must be an absolute http(s) URL, e.g. "https://example.com/logo.png"')
        return v

    @field_validator("accent_color")
    @classmethod
    def _validate_accent_color(cls, v):
        if v is None:
            return None
        if not _HEX_COLOR_PATTERN.match(v):
            raise ValueError('accent_color must be a 6-digit hex color, e.g. "#7a2e2e"')
        return v


def _validate_exact_origin(v: str) -> str:
    """
    Enforces exactly "scheme://host[:port]" — the literal shape a
    browser's Origin header always takes (no path, query, fragment, or
    trailing slash) — and normalizes scheme/host to lowercase for
    case-insensitive comparison (browsers always send lowercase anyway;
    this is purely defensive). No wildcard subdomains in v1: this
    accepts one exact origin per call, never a pattern.
    """
    parsed = urlparse(v.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError('origin must start with "http://" or "https://"')
    if not parsed.hostname:
        raise ValueError("origin must include a host")
    if "*" in parsed.hostname:
        raise ValueError("origin must not contain a wildcard — register each exact host separately")
    if parsed.path or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(
            'origin must be exactly "scheme://host[:port]" — no path, query, '
            "fragment, or trailing slash"
        )
    normalized = f"{parsed.scheme.lower()}://{parsed.hostname.lower()}"
    if parsed.port:
        normalized += f":{parsed.port}"
    return normalized


WIDGET_ORIGIN_MAX_LENGTH = 255


class WidgetAllowedOriginCreate(BaseModel):
    origin: str = Field(..., min_length=1, max_length=WIDGET_ORIGIN_MAX_LENGTH)

    @field_validator("origin")
    @classmethod
    def _check_origin(cls, v):
        return _validate_exact_origin(v)


class WidgetAllowedOriginOut(BaseModel):
    id: int
    origin: str
    created_at: datetime

    model_config = {"from_attributes": True}


class WidgetConfigOut(BaseModel):
    id: int
    restaurant_id: int
    widget_key: str
    welcome_message: Optional[str] = None
    primary_language: str
    logo_url: Optional[str] = None
    accent_color: Optional[str] = None
    booking_enabled: bool
    is_active: bool
    created_at: datetime
    allowed_origins: List[WidgetAllowedOriginOut] = []

    model_config = {"from_attributes": True}


# --- Public widget config (Stage 4 Phase B) ---
# Unlike WidgetConfigOut above (an authenticated admin view), this is
# the shape returned by the PUBLIC, unauthenticated
# GET /widget/{widget_key}/config (see app/routers/widget.py). It is
# deliberately NOT built via model_config={"from_attributes": True}
# passthrough of the WidgetConfig ORM row — every field here is set by
# hand at the call site, so a future column added to WidgetConfig or
# Restaurant can never leak through this endpoint just because the ORM
# object gained a new attribute. restaurant_id, WidgetConfig.id,
# is_active, and created_at are deliberately never included — none of
# them are public-safe or useful to an unauthenticated caller.

class WidgetPublicConfigOut(BaseModel):
    widget_key: str
    restaurant_name: str
    welcome_message: str
    primary_language: str
    logo_url: Optional[str] = None
    accent_color: Optional[str] = None
    booking_enabled: bool


# --- Public widget chat (Stage 4 Phase C) ---
# Deliberately minimal: no restaurant_id (tenant is resolved entirely
# server-side from the widget_key in the URL — see
# app/routers/widget.py), no history (a brand-new conversation always
# starts empty; there is no legacy client to stay compatible with here,
# unlike ChatRequest), and no conversation_token field — that's read
# from the X-Conversation-Token request header instead, so it never
# needs to appear in this body at all.

class WidgetChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=CHAT_MESSAGE_MAX_LENGTH)


# --- Conversations (Stage 3 Step 5: persistence) ---
# Read-only admin views. public_token is deliberately never included —
# it's the anonymous-customer-facing handle, not something an admin
# (who already has the integer id) needs or should be able to read back.

class ConversationOut(BaseModel):
    id: int
    restaurant_id: int
    channel: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    triggered_tool_call: bool
    created_at: datetime

    model_config = {"from_attributes": True}