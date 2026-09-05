"""
Knowledge retrieval layer.

This is the most important file in Stage 1 for preventing hallucination.

The rule is simple: the LLM is NEVER allowed to just "know" the menu,
hours, or FAQs from its own training. Instead, every time a customer
sends a message, we pull the restaurant's ACTUAL data out of the
database and hand it to the LLM as context, with strict instructions
to only answer from that context.

If a fact isn't in this context, the LLM has been instructed (see
llm.py) to say it doesn't have that information, rather than guess.

Stage 2b adds two read-only additions to that same context, still
under the same rule — everything here is a real fact computed from the
database, never invented:
  - CURRENT DATE, so relative phrases ("today", "tomorrow", "Saturday")
    resolve correctly.
  - An AVAILABILITY SUMMARY for the next few days, aggregated from
    app/booking.py's data (confirmed bookings + seating_capacity) so the
    assistant can discuss availability with real numbers.
This does NOT give the assistant the ability to create or change a
booking through chat — that's Stage 2c. The existing system prompt rule
in llm.py (booking isn't handled through chat yet) still applies; this
just lets the assistant answer availability *questions* honestly in the
meantime, using app/booking.py's own seating_capacity concept rather
than a second, separate notion of "availability".
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session
from . import models

# How many days ahead the availability summary covers — deliberately
# short to keep the context concise, per Stage 2b's brief.
AVAILABILITY_SUMMARY_DAYS = 7


def build_restaurant_context(db: Session, restaurant_id: int) -> str:
    """
    Fetches everything we know about a restaurant and formats it as a
    plain-text block to feed into the LLM's system prompt.
    Returns an empty-ish message if the restaurant doesn't exist, so the
    system prompt logic can handle that gracefully.
    """
    restaurant = (
        db.query(models.Restaurant)
        .filter(models.Restaurant.id == restaurant_id)
        .first()
    )

    if not restaurant:
        return "NO RESTAURANT DATA FOUND FOR THIS ID."

    lines = []

    today = date.today()
    lines.append(f"CURRENT DATE: {today.isoformat()} ({today.strftime('%A')})")

    # --- Basic info ---
    lines.append(f"\nRESTAURANT NAME: {restaurant.name}")
    lines.append(f"ADDRESS: {restaurant.address}")
    lines.append(f"PHONE: {restaurant.phone}")
    lines.append(f"EMAIL: {restaurant.email}")
    if restaurant.map_link:
        lines.append(f"MAP LINK: {restaurant.map_link}")
    if restaurant.parking_notes:
        lines.append(f"PARKING NOTES: {restaurant.parking_notes}")

    # --- Opening hours ---
    lines.append("\nOPENING HOURS:")
    hours = (
        db.query(models.OpeningHours)
        .filter(models.OpeningHours.restaurant_id == restaurant_id)
        .all()
    )
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    hours_sorted = sorted(hours, key=lambda h: day_order.index(h.day_of_week) if h.day_of_week in day_order else 99)
    for h in hours_sorted:
        if h.is_closed:
            lines.append(f"  {h.day_of_week}: Closed")
        else:
            lines.append(f"  {h.day_of_week}: {h.open_time} - {h.close_time}")

    # --- Availability summary (read-only; booking creation is not
    # wired up to chat yet — see the module docstring above) ---
    lines.append(f"\nAVAILABILITY SUMMARY (next {AVAILABILITY_SUMMARY_DAYS} days):")
    hours_by_day = {h.day_of_week: h for h in hours}
    window_end = today + timedelta(days=AVAILABILITY_SUMMARY_DAYS - 1)
    booked_seats_by_date = {}
    confirmed_bookings = (
        db.query(models.Booking)
        .filter(
            models.Booking.restaurant_id == restaurant_id,
            models.Booking.status == "confirmed",
            models.Booking.booking_date >= today,
            models.Booking.booking_date <= window_end,
        )
        .all()
    )
    for booking in confirmed_bookings:
        booked_seats_by_date[booking.booking_date] = (
            booked_seats_by_date.get(booking.booking_date, 0) + booking.party_size
        )

    for offset in range(AVAILABILITY_SUMMARY_DAYS):
        day = today + timedelta(days=offset)
        day_name = day.strftime("%A")
        day_hours = hours_by_day.get(day_name)
        label = f"  {day.isoformat()} ({day_name})"
        if not day_hours or day_hours.is_closed:
            lines.append(f"{label}: Closed")
            continue
        booked = booked_seats_by_date.get(day, 0)
        lines.append(
            f"{label}: Open {day_hours.open_time}-{day_hours.close_time}. "
            f"{booked} of {restaurant.seating_capacity} seats already reserved that day "
            f"(spread across different times, not necessarily all at once)."
        )

    # --- Menu ---
    lines.append("\nMENU:")
    menu_items = (
        db.query(models.MenuItem)
        .filter(models.MenuItem.restaurant_id == restaurant_id)
        .all()
    )
    categories = {}
    for item in menu_items:
        categories.setdefault(item.category, []).append(item)

    for category, items in categories.items():
        lines.append(f"  {category}:")
        for item in items:
            tag_str = f" [{item.dietary_tags}]" if item.dietary_tags else ""
            desc_str = f" - {item.description}" if item.description else ""
            lines.append(f"    - {item.name}: £{item.price:.2f}{desc_str}{tag_str}")

    # --- FAQs ---
    lines.append("\nFREQUENTLY ASKED QUESTIONS:")
    faqs = (
        db.query(models.FAQ)
        .filter(models.FAQ.restaurant_id == restaurant_id)
        .all()
    )
    for faq in faqs:
        lines.append(f"  Q: {faq.question}")
        lines.append(f"  A: {faq.answer}")

    return "\n".join(lines)
