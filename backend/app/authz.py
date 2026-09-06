"""
Restaurant-scoping enforcement (Stage 3 Step 3 — multi-tenant
authorization).

app/auth.py answers "who is calling" (AdminIdentity). This module
answers "may that identity touch this particular restaurant_id" — the
one check every restaurant-scoped admin/booking route must run before
doing anything else, so it lives in exactly one place rather than being
reimplemented per handler.

A superadmin (AdminIdentity.is_superadmin) may access every restaurant.
A restaurant-scoped admin may only access restaurants explicitly
granted via AdminRestaurantAccess.

Deliberately returns 404 "Restaurant not found" in BOTH cases — the
restaurant truly doesn't exist, or it exists but this caller isn't
scoped to it — rather than a 403 for the latter. This is not laziness:
a 403 would confirm to a caller that a given restaurant_id exists even
though they can't touch it, letting them enumerate other tenants' IDs.
A uniform 404 leaks nothing beyond what an unauthenticated caller could
already tell, and is exactly the response every existing test already
expects for a genuinely-missing restaurant, so this is a zero-behavior-
change fix for legitimate (in-scope) callers.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import models
from .auth import AdminIdentity

_NOT_FOUND = HTTPException(status_code=404, detail="Restaurant not found")


def require_restaurant_access(
    restaurant_id: int, db: Session, current_admin: AdminIdentity
) -> models.Restaurant:
    """Returns the Restaurant row if it exists AND current_admin may
    access it; otherwise raises 404. Every restaurant-scoped route
    should call this as its first step."""
    restaurant = (
        db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    )
    if not restaurant or not current_admin.may_access(restaurant_id):
        raise _NOT_FOUND
    return restaurant
