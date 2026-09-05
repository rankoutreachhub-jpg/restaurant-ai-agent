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
  - the restaurant is closed that day, or the slot falls outside that
    day's opening hours (checked against the OpeningHours table), or
  - the combined party size of every other overlapping, non-cancelled
    booking that day plus this one would exceed the restaurant's
    seating_capacity.

KNOWN LIMITATION: SQLite has no range/exclusion constraint, so this is a
check-then-insert rather than a database-enforced guarantee. Fine at
this MVP's scale (single process, already rate-limited) — the same
category of tradeoff as the in-memory rate limiter in app/rate_limit.py.
"""

import logging
from datetime import date as date_type, datetime, time as time_type
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from . import models, schemas

logger = logging.getLogger(__name__)

BOOKING_DURATION_MINUTES = 90


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


def check_availability(
    db: Session,
    restaurant: models.Restaurant,
    booking_date: date_type,
    booking_time: time_type,
    party_size: int,
    exclude_booking_id: Optional[int] = None,
) -> None:
    """Raises BookingConflictError if the slot can't be accommodated."""
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
