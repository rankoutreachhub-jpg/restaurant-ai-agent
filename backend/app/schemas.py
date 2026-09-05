"""
Pydantic schemas define the shape of data going in and out of the API.
FastAPI uses these to validate requests and to auto-generate the
interactive API docs at /docs.
"""

from datetime import date as date_type, time as time_type

from pydantic import BaseModel, EmailStr, Field, field_validator
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