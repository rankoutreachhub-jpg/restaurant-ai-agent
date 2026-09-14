"""
Booking business logic: availability checking and booking creation/
updates.

This is the single place that decides whether a booking can be made —
routers/bookings.py (admin management, Stage 2a) and, later, AI-assisted
booking through /chat (Stage 2c) both call into these same functions, so
there is exactly one code path that can ever write a booking or judge a
slot free, and the two can never disagree about availability.

Capacity-based overlap prevention (Stage 2a's chosen model — there is no
per-table concept in this schema): each booking occupies a fixed
BOOKING_DURATION_MINUTES window starting at its booking_time. A new
booking is rejected if:
  - the requested date+time is today, but that time has already passed
    (Final QA Audit finding C2 — see RESTAURANT_TIMEZONE below), or
  - the restaurant is closed that day, or the slot falls outside that
    day's opening hours (checked against the OpeningHours table), or
  - the combined party size of every other overlapping, non-cancelled
    booking that day plus this one would exceed the restaurant's
    seating_capacity.

create_booking() additionally rejects an exact duplicate — the same
customer booking the same restaurant/date/time twice (Final QA Audit
finding C1 — see _find_duplicate_booking below).

KNOWN LIMITATION: SQLite has no range/exclusion constraint, so this is a
check-then-insert rather than a database-enforced guarantee, for both
the capacity check above and the duplicate check below. Fine at this
MVP's scale (single process, already rate-limited) — the same category
of tradeoff as the in-memory rate limiter in app/rate_limit.py; a
concurrent double-submit slipping past both checks is an accepted,
unlikely edge case, not something worth a DB-level lock/constraint here.
"""

import logging
import re
from datetime import date as date_type, datetime, time as time_type
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from . import models, schemas

logger = logging.getLogger(__name__)

BOOKING_DURATION_MINUTES = 90

# MVP timezone assumption (Final QA Audit finding C2): every restaurant
# using this app today is UK-based (see seed_data.py's "The Kings Arms,
# Winchester" and the UK-English system prompt in app/llm.py) and there
# is no per-restaurant timezone column anywhere in the schema. "Is this
# same-day booking's time already in the past" must be judged in the
# restaurant's own local time, not the server's — a naive server clock
# (this process may run in a UTC container, e.g. on Railway) would
# misjudge the day boundary and the current time-of-day by up to an hour
# whenever the UK is on British Summer Time (late March-late October).
# Using the IANA "Europe/London" zone via the standard-library zoneinfo
# module (not a raw UTC+0/+1 guess) means this stays correct across the
# GMT/BST transition automatically, without a new hand-rolled offset
# table. Genuine multi-timezone support (a per-restaurant timezone
# field) is explicitly out of scope for this fix.
RESTAURANT_TIMEZONE = ZoneInfo("Europe/London")


class BookingConflictError(Exception):
    """Raised when a requested booking can't be accommodated — closed,
    outside opening hours, or over capacity for that time window."""


def _to_minutes(t: time_type) -> int:
    return t.hour * 60 + t.minute


def _interval(t: time_type) -> Tuple[int, int]:
    start = _to_minutes(t)
    return start, start + BOOKING_DURATION_MINUTES


def _parse_hhmm(value: str) -> time_type:
    return datetime.strptime(value, "%H:%M").time()


def _current_restaurant_time() -> datetime:
    """'Now', in RESTAURANT_TIMEZONE — the one place that reads the
    real clock for same-day booking checks, so a test can monkeypatch
    this single function to control "now" without needing to fake the
    system clock itself."""
    return datetime.now(RESTAURANT_TIMEZONE)


def _is_same_day_time_already_past(
    booking_date: date_type, booking_time: time_type, *, now: Optional[datetime] = None
) -> bool:
    """True only when booking_date is today (in RESTAURANT_TIMEZONE) AND
    booking_time is strictly earlier than the current time-of-day — a
    future date is never affected, and a same-day time still to come is
    never affected. `now` is normally omitted (real current time); tests
    pass a fixed datetime to check the boundary precisely."""
    now = now or _current_restaurant_time()
    return booking_date == now.date() and booking_time < now.time()


# Formatting characters stripped for duplicate-guard comparison ONLY —
# never applied to what's actually stored (Booking.phone keeps whatever
# the customer typed). Deliberately loose: schemas.BookingCreate.phone
# has no format validation of its own (any 1-30 character string is
# accepted), so this only removes clearly-cosmetic differences (spaces,
# hyphens, parentheses, dots) between two submissions of what is
# otherwise the same number — it does not attempt real phone-number
# parsing/validation (unlike app/phone.py's normalize_whatsapp_phone,
# which is E.164-only and raises on anything else; that's too strict for
# this best-effort, never-raising duplicate check).
_PHONE_STRIP_CHARS = str.maketrans("", "", " -().")


def _normalize_phone(phone: str) -> str:
    return (phone or "").strip().translate(_PHONE_STRIP_CHARS).lower()


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _find_duplicate_booking(
    db: Session,
    restaurant_id: int,
    phone: str,
    email: str,
    booking_date: date_type,
    booking_time: time_type,
) -> Optional[models.Booking]:
    """
    Returns an existing CONFIRMED booking for this exact
    (restaurant, date, time) that already belongs to the same customer,
    or None. "Same customer" (Final QA Audit finding C1) means the
    normalized phone matches OR the normalized email matches — matching
    on either alone is enough, since a customer's phone or email can
    each vary slightly (formatting, casing) between two submissions of
    what's genuinely a repeat booking, and two DIFFERENT customers
    coincidentally sharing both the same slot AND the same phone/email
    is not a realistic false-positive risk.

    Only CONFIRMED bookings count — a cancelled booking never blocks a
    genuine new booking for the same slot, mirroring check_availability's
    own "only confirmed bookings occupy capacity" rule.

    Deliberately exact-match on (booking_date, booking_time), not a
    window — a returning customer booking a different time (or a
    different date) is never treated as a duplicate.
    """
    normalized_phone = _normalize_phone(phone)
    normalized_email = _normalize_email(email)

    candidates = (
        db.query(models.Booking)
        .filter(
            models.Booking.restaurant_id == restaurant_id,
            models.Booking.booking_date == booking_date,
            models.Booking.booking_time == booking_time,
            models.Booking.status == "confirmed",
        )
        .all()
    )
    for existing in candidates:
        if normalized_phone and _normalize_phone(existing.phone) == normalized_phone:
            return existing
        if normalized_email and _normalize_email(existing.email) == normalized_email:
            return existing
    return None


def check_availability(
    db: Session,
    restaurant: models.Restaurant,
    booking_date: date_type,
    booking_time: time_type,
    party_size: int,
    exclude_booking_id: Optional[int] = None,
) -> None:
    """Raises BookingConflictError if the slot can't be accommodated."""
    if _is_same_day_time_already_past(booking_date, booking_time):
        raise BookingConflictError("That time has already passed today.")

    day_name = booking_date.strftime("%A")

    opening = (
        db.query(models.OpeningHours)
        .filter(
            models.OpeningHours.restaurant_id == restaurant.id,
            models.OpeningHours.day_of_week == day_name,
        )
        .first()
    )
    if not opening or opening.is_closed:
        raise BookingConflictError(f"The restaurant is closed on {day_name}s.")

    open_time = _parse_hhmm(opening.open_time)
    close_time = _parse_hhmm(opening.close_time)
    requested_start, requested_end = _interval(booking_time)

    if requested_start < _to_minutes(open_time) or requested_end > _to_minutes(close_time):
        raise BookingConflictError(
            f"Requested time is outside opening hours on {day_name} "
            f"({opening.open_time}-{opening.close_time})."
        )

    existing_query = db.query(models.Booking).filter(
        models.Booking.restaurant_id == restaurant.id,
        models.Booking.booking_date == booking_date,
        models.Booking.status == "confirmed",
    )
    if exclude_booking_id is not None:
        existing_query = existing_query.filter(models.Booking.id != exclude_booking_id)

    total_party_size = party_size
    for existing in existing_query.all():
        existing_start, existing_end = _interval(existing.booking_time)
        if existing_start < requested_end and requested_start < existing_end:
            total_party_size += existing.party_size

    if total_party_size > restaurant.seating_capacity:
        raise BookingConflictError(
            f"Sorry, we don't have space for a party of {party_size} at that time."
        )


def create_booking(
    db: Session, restaurant: models.Restaurant, data: schemas.BookingCreate
) -> models.Booking:
    check_availability(db, restaurant, data.booking_date, data.booking_time, data.party_size)

    duplicate = _find_duplicate_booking(
        db, restaurant.id, data.phone, data.email, data.booking_date, data.booking_time
    )
    if duplicate is not None:
        raise BookingConflictError(
            "It looks like you already have a booking for this restaurant at this date and time."
        )

    booking = models.Booking(
        restaurant_id=restaurant.id,
        customer_name=data.customer_name,
        phone=data.phone,
        email=data.email,
        booking_date=data.booking_date,
        booking_time=data.booking_time,
        party_size=data.party_size,
        notes=data.notes,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    # Never log customer_name/phone/email — only the operational facts.
    logger.info(
        "Booking created (id=%s restaurant_id=%s date=%s time=%s party_size=%s)",
        booking.id,
        restaurant.id,
        booking.booking_date,
        booking.booking_time,
        booking.party_size,
    )
    return booking


def update_booking(
    db: Session,
    restaurant: models.Restaurant,
    booking: models.Booking,
    data: schemas.BookingUpdate,
) -> models.Booking:
    updates = data.model_dump(exclude_unset=True)

    final_status = updates.get("status", booking.status)
    changes_slot = any(field in updates for field in ("booking_date", "booking_time", "party_size"))
    becoming_confirmed = booking.status != "confirmed" and final_status == "confirmed"

    if final_status == "confirmed" and (changes_slot or becoming_confirmed):
        new_date = updates.get("booking_date", booking.booking_date)
        new_time = updates.get("booking_time", booking.booking_time)
        new_party_size = updates.get("party_size", booking.party_size)
        # Excludes this booking's own current row so it doesn't conflict
        # with itself when re-checked.
        check_availability(
            db, restaurant, new_date, new_time, new_party_size, exclude_booking_id=booking.id
        )

    for field, value in updates.items():
        setattr(booking, field, value)

    db.commit()
    db.refresh(booking)

    logger.info(
        "Booking updated (id=%s restaurant_id=%s status=%s)",
        booking.id,
        restaurant.id,
        booking.status,
    )
    return booking
