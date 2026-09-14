"""
Same-day past-time booking protection (Final QA Audit finding C2) — see
app/booking.py:_is_same_day_time_already_past and the RESTAURANT_TIMEZONE
docstring above it for the single-timezone MVP assumption this exercises
(every restaurant is treated as UK-based; "now" is computed in
Europe/London via the standard-library zoneinfo module, not the
server's own naive clock, specifically so this stays correct across the
GMT/BST transition).

Real, relative-to-actual-today dates (today/tomorrow/yesterday) are used
throughout, unlike every other booking test file, which deliberately
uses a large fixed offset from today to avoid the shared test-session
database (see tests/test_booking_service.py's own docstring). This file
is the one place that must use near-term real dates, since that is
exactly what it's testing — no other test file touches these dates, so
there is no collision risk.

"Now" is controlled via monkeypatching app.booking._current_restaurant_time
(see that function's own docstring) rather than faking the system clock,
so each test is deterministic regardless of when it actually runs. Where
a test doesn't need a controlled "now" (the schema-level past-date
check, and the opening-hours-safe time used throughout), the real clock
is left alone.

12:00 is used as the "safe" booking time throughout (as in
test_booking_service.py) because every day in the seeded restaurant's
opening hours (app/seed_data.py) opens at 11:00 or 12:00 and closes no
earlier than 21:00 — so a 90-minute booking starting at 12:00 always
falls within hours, regardless of which real weekday "today"/"tomorrow"
happens to be when this file runs.
"""

from datetime import date, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from app import booking, models, schemas
from app.booking import BookingConflictError, RESTAURANT_TIMEZONE, create_booking

_SAFE_TIME = time(12, 0)


def _restaurant(db):
    return db.query(models.Restaurant).first()


def _make_booking_data(**overrides):
    defaults = dict(
        customer_name="Same-Day Test Customer",
        phone="01111 222333",
        email="same-day-test@example.com",
        booking_date=date.today(),
        booking_time=_SAFE_TIME,
        party_size=2,
        notes=None,
    )
    defaults.update(overrides)
    return schemas.BookingCreate(**defaults)


def _freeze_now(monkeypatch, fixed: datetime) -> None:
    monkeypatch.setattr(booking, "_current_restaurant_time", lambda: fixed)


def _today_at(hour: int, minute: int = 0) -> datetime:
    today = date.today()
    return datetime(today.year, today.month, today.day, hour, minute, tzinfo=RESTAURANT_TIMEZONE)


def test_today_with_a_future_time_is_allowed(db, monkeypatch):
    restaurant = _restaurant(db)
    _freeze_now(monkeypatch, _today_at(8, 0))

    result = create_booking(
        db, restaurant,
        _make_booking_data(
            booking_date=date.today(), booking_time=_SAFE_TIME,
            phone="c2-future-01", email="c2-future-01@example.com",
        ),
    )
    assert result.id is not None


def test_today_with_a_past_time_is_rejected(db, monkeypatch):
    restaurant = _restaurant(db)
    _freeze_now(monkeypatch, _today_at(15, 0))

    with pytest.raises(BookingConflictError, match="already passed"):
        create_booking(
            db, restaurant,
            _make_booking_data(
                booking_date=date.today(), booking_time=_SAFE_TIME,
                phone="c2-past-01", email="c2-past-01@example.com",
            ),
        )


def test_tomorrow_with_a_valid_opening_hours_time_is_allowed(db, monkeypatch):
    restaurant = _restaurant(db)
    # "now" fixed late in the day — must have no bearing on a booking
    # dated tomorrow, only on one dated today.
    _freeze_now(monkeypatch, _today_at(23, 0))
    tomorrow = date.today() + timedelta(days=1)

    result = create_booking(
        db, restaurant,
        _make_booking_data(
            booking_date=tomorrow, booking_time=_SAFE_TIME,
            phone="c2-tomorrow-01", email="c2-tomorrow-01@example.com",
        ),
    )
    assert result.id is not None


def test_yesterday_is_rejected():
    """Pre-existing protection (schemas.BookingCreate's own
    booking_date validator, unchanged by this fix) — locked in here as
    part of C2's required test list, not a new behavior. A past date is
    rejected at construction time, before booking.py is ever reached."""
    yesterday = date.today() - timedelta(days=1)
    with pytest.raises(ValidationError):
        schemas.BookingCreate(
            customer_name="Yesterday Test",
            phone="c2-yesterday-01",
            email="c2-yesterday-01@example.com",
            booking_date=yesterday,
            booking_time=_SAFE_TIME,
            party_size=2,
        )


def test_boundary_around_the_current_time(db, monkeypatch):
    """Locks in the exact boundary rule: booking_time < now.time() is
    what's rejected (a strict less-than) — a booking starting exactly
    now, or any time still to come, is allowed; only a genuinely earlier
    time is rejected."""
    restaurant = _restaurant(db)
    _freeze_now(monkeypatch, _today_at(12, 0))
    today = date.today()

    # Exactly "now" — not yet past, so allowed.
    at_now = create_booking(
        db, restaurant,
        _make_booking_data(
            booking_date=today, booking_time=time(12, 0),
            phone="c2-boundary-eq", email="c2-boundary-eq@example.com",
        ),
    )
    assert at_now.id is not None

    # One minute before "now" — already past, rejected.
    with pytest.raises(BookingConflictError, match="already passed"):
        create_booking(
            db, restaurant,
            _make_booking_data(
                booking_date=today, booking_time=time(11, 59),
                phone="c2-boundary-past", email="c2-boundary-past@example.com",
            ),
        )

    # One minute after "now" — still to come, allowed.
    after_now = create_booking(
        db, restaurant,
        _make_booking_data(
            booking_date=today, booking_time=time(12, 1),
            phone="c2-boundary-future", email="c2-boundary-future@example.com",
        ),
    )
    assert after_now.id is not None


def test_future_dated_booking_is_never_affected_by_the_same_day_check(db, monkeypatch):
    """Regression check: a booking on a genuinely future date is
    unaffected by this fix regardless of what 'now' is, exactly as
    before — mirrors test_booking_service.py's own far-future-anchor
    convention, just confirming this new check doesn't reach it."""
    restaurant = _restaurant(db)
    _freeze_now(monkeypatch, _today_at(23, 59))
    far_future = date.today() + timedelta(days=60)

    result = create_booking(
        db, restaurant,
        _make_booking_data(
            booking_date=far_future, booking_time=_SAFE_TIME,
            phone="c2-far-future", email="c2-far-future@example.com",
        ),
    )
    assert result.id is not None
