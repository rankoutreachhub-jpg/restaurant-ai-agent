"""
Unit tests for the booking service layer (app/booking.py): capacity-
based overlap prevention, opening-hours enforcement, and update
semantics — independent of the HTTP layer.

Date allocation: the DB is shared across the whole test session (see
conftest.py), so every test here uses a date far from "today" that no
other test file touches, and each "normal" test gets a distinct week
from a fixed anchor (so they all fall on the same weekday and never
collide with each other's bookings). The one test that needs a *closed*
day uses a different, dedicated weekday so mutating its OpeningHours
row can never affect any other test.
"""

import itertools
from datetime import date, time, timedelta

import pytest

from app import models, schemas
from app.booking import BookingConflictError, create_booking, update_booking

# Far enough out to avoid tests/test_booking_admin.py's date range, and
# each test below only advances by a handful of weeks — nowhere near the
# 365-day cap in schemas.BOOKING_MAX_ADVANCE_DAYS.
_ANCHOR = date.today() + timedelta(days=30)
_counter = itertools.count(1)


def _fresh_date() -> date:
    """A distinct future date; always the same weekday as _ANCHOR."""
    return _ANCHOR + timedelta(weeks=next(_counter))


# A different weekday from every _fresh_date() result, reserved
# exclusively for the closed-day test below.
_CLOSED_DATE = _ANCHOR + timedelta(days=1)
_CLOSED_DAY_NAME = _CLOSED_DATE.strftime("%A")

# 12:00-19:00 is within every seeded day's opening hours (earliest open
# 11:00, latest relevant close 21:00 on Sunday, both with room for a
# 90-minute booking), regardless of which weekday _ANCHOR lands on.
_SAFE_TIME = time(12, 0)
# Before every seeded opening time (earliest is 11:00) — always outside
# hours, regardless of weekday.
_OUTSIDE_HOURS_TIME = time(6, 0)


def _restaurant(db):
    return db.query(models.Restaurant).first()


def _make_booking_data(**overrides):
    defaults = dict(
        customer_name="Jane Doe",
        phone="01234 567890",
        email="jane@example.com",
        booking_date=_fresh_date(),
        booking_time=_SAFE_TIME,
        party_size=4,
        notes=None,
    )
    defaults.update(overrides)
    return schemas.BookingCreate(**defaults)


def test_create_booking_succeeds_within_capacity(db):
    restaurant = _restaurant(db)
    booking = create_booking(db, restaurant, _make_booking_data(party_size=4))
    assert booking.id is not None
    assert booking.status == "confirmed"


def test_create_booking_rejected_when_over_capacity(db):
    restaurant = _restaurant(db)
    d = _fresh_date()
    half = restaurant.seating_capacity // 2

    create_booking(db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(19, 0)))
    create_booking(
        db,
        restaurant,
        _make_booking_data(
            booking_date=d, party_size=restaurant.seating_capacity - half, booking_time=time(19, 15)
        ),
    )
    # Capacity is now fully committed for the overlapping window.
    with pytest.raises(BookingConflictError):
        create_booking(db, restaurant, _make_booking_data(booking_date=d, party_size=1, booking_time=time(19, 30)))


def test_non_overlapping_bookings_do_not_count_against_each_other(db):
    restaurant = _restaurant(db)
    d = _fresh_date()
    half = restaurant.seating_capacity // 2

    create_booking(db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(12, 0)))
    # 90-minute slots: 12:00-13:30 vs 14:00-15:30 don't overlap.
    booking = create_booking(
        db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(14, 0))
    )
    assert booking.id is not None


def test_create_booking_rejected_when_restaurant_closed_that_day(db):
    restaurant = _restaurant(db)
    hours = (
        db.query(models.OpeningHours)
        .filter(
            models.OpeningHours.restaurant_id == restaurant.id,
            models.OpeningHours.day_of_week == _CLOSED_DAY_NAME,
        )
        .first()
    )
    # OpeningHours is a weekly recurring schedule (keyed by day-of-week
    # name, not a specific calendar date), and the DB is shared across
    # the whole test session — restore this so no other test whose date
    # happens to land on the same weekday is affected.
    original_is_closed = hours.is_closed
    hours.is_closed = True
    db.commit()
    try:
        with pytest.raises(BookingConflictError):
            create_booking(db, restaurant, _make_booking_data(booking_date=_CLOSED_DATE, booking_time=time(19, 0)))
    finally:
        hours.is_closed = original_is_closed
        db.commit()


def test_create_booking_rejected_outside_opening_hours(db):
    restaurant = _restaurant(db)
    with pytest.raises(BookingConflictError):
        create_booking(db, restaurant, _make_booking_data(booking_time=_OUTSIDE_HOURS_TIME))


def test_cancelled_booking_frees_capacity(db):
    restaurant = _restaurant(db)
    d = _fresh_date()
    half = restaurant.seating_capacity // 2

    first = create_booking(
        db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(18, 0))
    )
    create_booking(db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(18, 15)))
    update_booking(db, restaurant, first, schemas.BookingUpdate(status="cancelled"))

    # first's share is now free again, even though the slot overlaps.
    new_booking = create_booking(
        db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(18, 30))
    )
    assert new_booking.id is not None


def test_update_booking_slot_is_rechecked_for_conflicts(db):
    restaurant = _restaurant(db)
    d = _fresh_date()
    half = restaurant.seating_capacity // 2

    create_booking(db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(18, 0)))
    create_booking(db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(18, 0)))
    other = create_booking(
        db, restaurant, _make_booking_data(booking_date=d, party_size=1, booking_time=time(21, 0))
    )

    with pytest.raises(BookingConflictError):
        update_booking(db, restaurant, other, schemas.BookingUpdate(booking_time=time(18, 0)))


def test_update_booking_non_slot_fields_do_not_trigger_recheck(db):
    restaurant = _restaurant(db)
    d = _fresh_date()
    booking = create_booking(
        db, restaurant, _make_booking_data(booking_date=d, party_size=10, booking_time=time(15, 0))
    )

    # Simulate capacity having been reduced (by an admin) after the
    # booking was made, so the booking's *current* slot would now fail
    # check_availability if it were re-run. Restored in `finally` since
    # `restaurant` is a shared row other tests in this session rely on.
    original_capacity = restaurant.seating_capacity
    restaurant.seating_capacity = 1
    db.commit()
    try:
        updated = update_booking(db, restaurant, booking, schemas.BookingUpdate(phone="09999 999999"))
        assert updated.phone == "09999 999999"
    finally:
        restaurant.seating_capacity = original_capacity
        db.commit()


def test_reactivating_a_cancelled_booking_is_rechecked(db):
    restaurant = _restaurant(db)
    d = _fresh_date()
    half = restaurant.seating_capacity // 2

    first = create_booking(
        db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(18, 0))
    )
    update_booking(db, restaurant, first, schemas.BookingUpdate(status="cancelled"))
    # Two other bookings now fill the slot `first` used to hold.
    create_booking(db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(18, 0)))
    create_booking(db, restaurant, _make_booking_data(booking_date=d, party_size=half, booking_time=time(18, 0)))

    with pytest.raises(BookingConflictError):
        update_booking(db, restaurant, first, schemas.BookingUpdate(status="confirmed"))
