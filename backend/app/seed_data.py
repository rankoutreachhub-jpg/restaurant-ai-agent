"""
Seed data.

This creates ONE example restaurant with realistic-looking data so you
can test the chatbot immediately, without needing a real client yet.
When you onboard a real restaurant later, you'll replace this data
(Stage 2+ will likely add an admin way to edit it — for Stage 1, editing
this file directly is the intended workflow).

This only runs once: if the restaurants table already has data, it
does nothing, so restarting the server won't duplicate rows.
"""

import logging

from sqlalchemy.orm import Session
from . import models

logger = logging.getLogger(__name__)


def seed_if_empty(db: Session):
    existing = db.query(models.Restaurant).first()
    if existing:
        logger.info("Restaurant data already present; skipping seed.")
        return  # Already seeded, do nothing

    restaurant = models.Restaurant(
        name="The Kings Arms",
        address="14 High Street, Winchester, Hampshire, SO23 9BW",
        phone="01962 123456",
        email="hello@thekingsarms-winchester.co.uk",
        map_link="https://maps.google.com/?q=The+Kings+Arms+Winchester",
        parking_notes="Free parking is available in the small car park behind the pub, "
                       "and there's a public car park on Jewry Street 3 minutes' walk away.",
        seating_capacity=40,
    )
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)

    # --- Opening hours ---
    hours_data = [
        ("Monday", "12:00", "22:00", False),
        ("Tuesday", "12:00", "22:00", False),
        ("Wednesday", "12:00", "22:00", False),
        ("Thursday", "12:00", "22:30", False),
        ("Friday", "12:00", "23:00", False),
        ("Saturday", "11:00", "23:00", False),
        ("Sunday", "12:00", "21:00", False),
    ]
    for day, open_t, close_t, closed in hours_data:
        db.add(models.OpeningHours(
            restaurant_id=restaurant.id,
            day_of_week=day,
            open_time=open_t,
            close_time=close_t,
            is_closed=closed,
        ))

    # --- Menu ---
    menu_data = [
        ("Starters", "Soup of the Day", "Served with crusty bread", 6.50, "vegetarian"),
        ("Starters", "Garlic King Prawns", "Served in a chilli and garlic butter", 8.95, None),
        ("Starters", "Whitebait", "Lightly floured and fried, tartare sauce", 7.25, None),
        ("Mains", "Fish and Chips", "Beer-battered cod, chunky chips, mushy peas", 14.95, None),
        ("Mains", "Steak and Ale Pie", "Slow-cooked beef in ale gravy, shortcrust pastry", 15.50, None),
        ("Mains", "Mushroom Risotto", "Wild mushrooms, parmesan, truffle oil", 13.50, "vegetarian, gluten-free"),
        ("Mains", "8oz Sirloin Steak", "Served with chips, grilled tomato, and peppercorn sauce", 22.00, "gluten-free"),
        ("Desserts", "Sticky Toffee Pudding", "With vanilla ice cream", 6.95, "vegetarian"),
        ("Desserts", "Cheese Board", "Selection of local cheeses, biscuits, chutney", 8.50, "vegetarian"),
        ("Drinks", "House Red Wine (175ml)", "Merlot", 5.95, None),
        ("Drinks", "Local Ale (Pint)", "Rotating selection from local breweries", 4.80, None),
    ]
    for category, name, description, price, tags in menu_data:
        db.add(models.MenuItem(
            restaurant_id=restaurant.id,
            category=category,
            name=name,
            description=description,
            price=price,
            dietary_tags=tags,
        ))

    # --- FAQs ---
    faq_data = [
        ("Do you allow dogs?", "Yes, well-behaved dogs are welcome in our bar area and garden."),
        ("Is there wheelchair access?", "Yes, we have step-free access at the main entrance and an accessible toilet."),
        ("Do you cater for allergies?", "Yes, please let a member of staff know about any allergies when you order — "
                                        "we take this very seriously and can talk you through safe options."),
        ("Do you have a garden?", "Yes, we have a large beer garden that's open (weather permitting) all year round."),
        ("Do you show sports?", "We show major football and rugby matches on our screens in the bar area."),
        ("Is there a kids' menu?", "Yes, we have a kids' menu available for children under 12."),
    ]
    for question, answer in faq_data:
        db.add(models.FAQ(
            restaurant_id=restaurant.id,
            question=question,
            answer=answer,
        ))

    db.commit()
    logger.info("Seeded initial restaurant data (%s).", restaurant.name)
