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
"""

from sqlalchemy.orm import Session
from . import models


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

    # --- Basic info ---
    lines.append(f"RESTAURANT NAME: {restaurant.name}")
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
