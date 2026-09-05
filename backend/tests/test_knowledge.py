"""
Stage 2b: knowledge.build_restaurant_context() gains a CURRENT DATE line
and a read-only AVAILABILITY SUMMARY, computed from real OpeningHours/
Booking data — no booking creation and no LLM changes involved. These
tests exercise the context builder directly, independent of /chat.

Date allocation: see tests/test_booking_service.py's module docstring
for why other test files use distant, isolated dates. This file is
different — the availability summary is always relative to *today*, so
its window (today..today+6) can't be moved. To avoid any risk of
leaking state between the two tests here that mutate shared data, each
uses its own offset within that window and restores what it changed in
a `finally` block.
"""

from datetime import date, timedelta

from app import models, schemas
from app.booking import create_booking
from app.knowledge import AVAILABILITY_SUMMARY_DAYS, build_restaurant_context


def _restaurant(db):
    return db.query(models.Restaurant).first()


def test_context_includes_current_date(db):
    restaurant = _restaurant(db)
    context = build_restaurant_context(db, restaurant.id)

    today = date.today()
    assert f"CURRENT DATE: {today.isoformat()} ({today.strftime('%A')})" in context


def test_context_still_includes_existing_sections(db):
    """Regression guard: Stage 2b must not disturb Stage 1's content."""
    restaurant = _restaurant(db)
    context = build_restaurant_context(db, restaurant.id)

    assert f"RESTAURANT NAME: {restaurant.name}" in context
    assert "OPENING HOURS:" in context
    assert "MENU:" in context
    assert "FREQUENTLY ASKED QUESTIONS:" in context


def test_availability_summary_lists_every_day_in_the_window(db):
    restaurant = _restaurant(db)
    context = build_restaurant_context(db, restaurant.id)

    assert f"AVAILABILITY SUMMARY (next {AVAILABILITY_SUMMARY_DAYS} days):" in context
    today = date.today()
    for offset in range(AVAILABILITY_SUMMARY_DAYS):
        day = today + timedelta(days=offset)
        assert f"{day.isoformat()} ({day.strftime('%A')})" in context


def test_availability_summary_shows_closed_day(db):
    restaurant = _restaurant(db)
    target_date = date.today() + timedelta(days=2)
    day_name = target_date.strftime("%A")

    hours = (
        db.query(models.OpeningHours)
        .filter(
            models.OpeningHours.restaurant_id == restaurant.id,
            models.OpeningHours.day_of_week == day_name,
        )
        .first()
    )
    original_is_closed = hours.is_closed
    hours.is_closed = True
    db.commit()
    try:
        context = build_restaurant_context(db, restaurant.id)
        assert f"{target_date.isoformat()} ({day_name}): Closed" in context
    finally:
        hours.is_closed = original_is_closed
        db.commit()


def test_availability_summary_reflects_confirmed_booking_seats(db):
    restaurant = _restaurant(db)
    target_date = date.today() + timedelta(days=3)

    create_booking(
        db,
        restaurant,
        schemas.BookingCreate(
            customer_name="Test Diner",
            phone="01234 000000",
            email="diner@example.com",
            booking_date=target_date,
            booking_time="13:00",
            party_size=5,
        ),
    )

    context = build_restaurant_context(db, restaurant.id)
    day_line = next(line for line in context.splitlines() if target_date.isoformat() in line)
    assert "Open" in day_line
    assert f"5 of {restaurant.seating_capacity} seats already reserved" in day_line


def test_availability_summary_ignores_cancelled_bookings(db):
    restaurant = _restaurant(db)
    target_date = date.today() + timedelta(days=4)

    booking = create_booking(
        db,
        restaurant,
        schemas.BookingCreate(
            customer_name="Cancelled Diner",
            phone="01234 111111",
            email="cancelled@example.com",
            booking_date=target_date,
            booking_time="13:30",
            party_size=6,
        ),
    )
    booking.status = "cancelled"
    db.commit()

    context = build_restaurant_context(db, restaurant.id)
    day_line = next(line for line in context.splitlines() if target_date.isoformat() in line)
    assert f"0 of {restaurant.seating_capacity} seats already reserved" in day_line
