"""
Restaurant onboarding completeness (Stage 3 Step 4): a brand-new
restaurant must be fully populatable — menu, FAQs, and all 7 days of
opening hours — using only authenticated HTTP calls, with ZERO direct
database writes for any of that child data. This is the concrete proof
that the gap flagged in the Stage 3 Step 3 PR ("no endpoint yet creates
a new restaurant's initial opening-hours rows... onboarding still needs
a direct DB insert") is now closed.
"""

from app import knowledge
from app.database import SessionLocal


def test_new_restaurant_can_be_fully_populated_through_apis_only(client, admin_headers):
    # 1. Onboard the restaurant itself — already API-only since Stage 3 Step 3.
    restaurant_response = client.post(
        "/admin/platform/restaurants",
        json={
            "name": "The Onboarding Complete Bistro",
            "address": "42 New Restaurant Lane",
            "phone": "01000 111222",
            "email": "hello@onboardingcomplete.example.com",
            "seating_capacity": 25,
        },
        headers=admin_headers,
    )
    assert restaurant_response.status_code == 201
    restaurant_id = restaurant_response.json()["restaurant"]["id"]

    # 2. Menu — already API-only since Stage 2a.
    menu_response = client.post(
        f"/admin/restaurant/{restaurant_id}/menu",
        json={"category": "Mains", "name": "Veggie Burger", "price": 11.50, "dietary_tags": "vegetarian"},
        headers=admin_headers,
    )
    assert menu_response.status_code == 200

    # 3. FAQs — new in Stage 3 Step 4.
    faq_response = client.post(
        f"/admin/restaurant/{restaurant_id}/faqs",
        json={"question": "Do you take walk-ins?", "answer": "Yes, subject to availability."},
        headers=admin_headers,
    )
    assert faq_response.status_code == 201

    # 4. Opening hours — new in Stage 3 Step 4. All 7 days in one call.
    days = [
        {"day_of_week": name, "open_time": "12:00", "close_time": "22:00", "is_closed": False}
        for name in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    ]
    days.append({"day_of_week": "Sunday", "is_closed": True})
    hours_response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": days},
        headers=admin_headers,
    )
    assert hours_response.status_code == 201
    assert len(hours_response.json()["opening_hours"]) == 7

    # --- Verification: everything is really there, queryable both via
    # the admin API and via the same knowledge-context builder /chat
    # uses — proving the API-created data is indistinguishable from
    # data created any other way. ---

    menu = client.get(f"/admin/restaurant/{restaurant_id}/menu", headers=admin_headers).json()
    assert any(item["name"] == "Veggie Burger" for item in menu)

    faqs = client.get(f"/admin/restaurant/{restaurant_id}/faqs", headers=admin_headers).json()
    assert any(f["question"] == "Do you take walk-ins?" for f in faqs)

    hours = client.get(f"/admin/restaurant/{restaurant_id}/opening-hours", headers=admin_headers).json()
    assert len(hours) == 7
    assert any(h["day_of_week"] == "Sunday" and h["is_closed"] for h in hours)
    assert any(h["day_of_week"] == "Monday" and h["open_time"] == "12:00" for h in hours)

    db = SessionLocal()
    try:
        context = knowledge.build_restaurant_context(db, restaurant_id)
    finally:
        db.close()
    assert "Veggie Burger" in context
    assert "Do you take walk-ins?" in context
    assert "Sunday: Closed" in context
