"""
Platform-level admin endpoints (Stage 3 Step 3 — multi-tenant
authorization): onboarding restaurants and issuing/managing
restaurant-scoped admin keys.

Every route here requires the platform superadmin key (ADMIN_API_KEY /
ADMIN_API_KEY_PREVIOUS, see app/auth.py) — a restaurant-scoped admin
key, no matter how many restaurants it's scoped to, is never accepted
here. This is what stops a restaurant admin from creating other admin
users or granting themselves access to a restaurant they don't already
have.

Plaintext admin-user API keys are only ever present in the response
body of the create and rotate-key endpoints below, exactly once each —
see app/admin_keys.py for why only a hash is stored, and app/models.py
(AdminUser) for the storage shape.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..admin_keys import generate_key
from ..auth import AdminIdentity, require_superadmin
from ..database import get_db
from ..onboarding_status import compute_restaurant_onboarding_status
from ..rate_limit import admin_rate_limiter
from ..subscriptions import create_subscription_for_restaurant
from ..widget_keys import generate_widget_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/platform",
    tags=["Admin - Platform"],
    dependencies=[Depends(admin_rate_limiter), Depends(require_superadmin)],
)


def _admin_user_out(admin_user: models.AdminUser) -> dict:
    return {
        "id": admin_user.id,
        "label": admin_user.label,
        "is_active": admin_user.is_active,
        "created_at": admin_user.created_at,
        "restaurant_ids": sorted(
            access.restaurant_id for access in admin_user.restaurant_access
        ),
    }


def _get_admin_user_or_404(db: Session, admin_user_id: int) -> models.AdminUser:
    admin_user = (
        db.query(models.AdminUser).filter(models.AdminUser.id == admin_user_id).first()
    )
    if not admin_user:
        raise HTTPException(status_code=404, detail="Admin user not found")
    return admin_user


def _get_restaurant_or_404(db: Session, restaurant_id: int) -> models.Restaurant:
    restaurant = (
        db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    )
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return restaurant


# =========================================================
# ONBOARD A RESTAURANT
# =========================================================

@router.post("/restaurants", status_code=201)
def create_restaurant(data: schemas.RestaurantCreate, db: Session = Depends(get_db)):
    restaurant = models.Restaurant(
        name=data.name,
        address=data.address,
        phone=data.phone,
        email=data.email,
        map_link=data.map_link,
        parking_notes=data.parking_notes,
        seating_capacity=data.seating_capacity,
    )
    db.add(restaurant)
    db.flush()  # assign restaurant.id without committing yet

    # Jantar SaaS Phase 2: every restaurant must have exactly one
    # Subscription row (Phase 1's documented gap for restaurants
    # created after that migration ran) — created here, in the same
    # transaction as the restaurant itself, so either both are
    # committed together or neither is.
    create_subscription_for_restaurant(db, restaurant, commit=False)

    # Onboarding hardening: every restaurant must also have exactly one
    # WidgetConfig row from the moment it's created, mirroring the
    # Subscription treatment above — same "commit=False, same
    # transaction" reasoning, so a restaurant can never end up with one
    # but not the other. is_active is explicitly False (overriding the
    # model's own default of True): widget_cors_middleware only blocks
    # *browser* cross-origin requests missing from WidgetAllowedOrigin
    # (see that module's docstring — a request with no Origin header,
    # e.g. curl or server-to-server, passes through untouched), so an
    # active-by-default widget_key would already be a fully working
    # public chat/booking endpoint the instant it's generated, before
    # the restaurant has configured anything. Every other field is left
    # unset so the model's own column defaults apply (primary_language
    # "en-GB", booking_enabled True) — no restaurant-specific branding
    # (welcome_message/logo_url/accent_color) is invented here; the
    # restaurant admin sets those from the existing Widget tab, whose
    # is_active checkbox already lets them flip this on when ready.
    widget_config = models.WidgetConfig(
        restaurant_id=restaurant.id,
        widget_key=generate_widget_key(),
        is_active=False,
    )
    db.add(widget_config)

    db.commit()
    db.refresh(restaurant)

    logger.info("Restaurant onboarded (id=%s)", restaurant.id)

    return {
        "message": "Restaurant created successfully",
        "restaurant": {
            "id": restaurant.id,
            "name": restaurant.name,
            "address": restaurant.address,
            "phone": restaurant.phone,
            "email": restaurant.email,
            "map_link": restaurant.map_link,
            "parking_notes": restaurant.parking_notes,
            "seating_capacity": restaurant.seating_capacity,
        },
    }


# =========================================================
# LIST RESTAURANTS (Stage 3 Step 6A: powers the dashboard's
# restaurant switcher/onboarding screen for a superadmin)
# =========================================================

@router.get("/restaurants", response_model=list[schemas.RestaurantWithSubscriptionOut])
def list_restaurants(db: Session = Depends(get_db)):
    restaurants = db.query(models.Restaurant).order_by(models.Restaurant.id).all()

    # Additive fields only (Jantar SaaS Phase 2) — looked up separately
    # rather than via a Restaurant.subscription relationship, so
    # models.Restaurant itself needs no change. A restaurant with no
    # subscription row (not expected after Phase 2, see
    # create_restaurant above, but possible for one onboarded in the
    # gap between the Phase 1 migration and this deploy) degrades to
    # null fields rather than a 500.
    subscriptions_by_restaurant_id = {
        s.restaurant_id: s
        for s in db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id.in_([r.id for r in restaurants]))
        .all()
    }

    return [
        schemas.RestaurantWithSubscriptionOut(
            id=r.id,
            name=r.name,
            address=r.address,
            phone=r.phone,
            email=r.email,
            map_link=r.map_link,
            parking_notes=r.parking_notes,
            seating_capacity=r.seating_capacity,
            plan_code=(subscriptions_by_restaurant_id[r.id].plan_code if r.id in subscriptions_by_restaurant_id else None),
            subscription_status=(subscriptions_by_restaurant_id[r.id].status if r.id in subscriptions_by_restaurant_id else None),
        )
        for r in restaurants
    ]


# =========================================================
# ONBOARDING READINESS CHECKLIST (superadmin-only, read-only — see
# app/onboarding_status.py for exactly which checks are computed and
# which ones are blocking vs. informational). Reuses this router's own
# require_superadmin dependency (declared above on `router`); no new
# authentication path and no new database table.
# =========================================================

@router.get(
    "/restaurants/onboarding-status",
    response_model=list[schemas.RestaurantOnboardingStatusOut],
)
def list_restaurants_onboarding_status(db: Session = Depends(get_db)):
    restaurants = db.query(models.Restaurant).order_by(models.Restaurant.id).all()
    return [compute_restaurant_onboarding_status(db, r) for r in restaurants]


@router.get(
    "/restaurants/{restaurant_id}/onboarding-status",
    response_model=schemas.RestaurantOnboardingStatusOut,
)
def get_restaurant_onboarding_status(restaurant_id: int, db: Session = Depends(get_db)):
    restaurant = _get_restaurant_or_404(db, restaurant_id)
    return compute_restaurant_onboarding_status(db, restaurant)


# =========================================================
# WHATSAPP NUMBER MAPPING (Stage 3 Step 6B: one WhatsApp
# phone_number_id per restaurant — see app/models.py:WhatsAppNumber)
# =========================================================

@router.post(
    "/restaurants/{restaurant_id}/whatsapp-number",
    response_model=schemas.WhatsAppNumberOut,
    status_code=201,
)
def set_whatsapp_number(
    restaurant_id: int, data: schemas.WhatsAppNumberCreate, db: Session = Depends(get_db)
):
    """
    Creates or replaces the ONE WhatsApp number mapping for this
    restaurant (v1 architecture: exactly one per restaurant — POST is
    an upsert, since there is nothing to PATCH partially and no
    separate create-only endpoint). Rejects (409) a phone_number_id
    already mapped to a DIFFERENT restaurant — phone_number_id is
    globally unique, so that would silently misroute every future
    message from that WhatsApp number.
    """
    _get_restaurant_or_404(db, restaurant_id)

    conflicting = (
        db.query(models.WhatsAppNumber)
        .filter(
            models.WhatsAppNumber.phone_number_id == data.phone_number_id,
            models.WhatsAppNumber.restaurant_id != restaurant_id,
        )
        .first()
    )
    if conflicting:
        raise HTTPException(
            status_code=409,
            detail="This WhatsApp phone_number_id is already mapped to a different restaurant.",
        )

    mapping = (
        db.query(models.WhatsAppNumber)
        .filter(models.WhatsAppNumber.restaurant_id == restaurant_id)
        .first()
    )
    if mapping:
        mapping.phone_number_id = data.phone_number_id
        mapping.display_phone_number = data.display_phone_number
    else:
        mapping = models.WhatsAppNumber(
            restaurant_id=restaurant_id,
            phone_number_id=data.phone_number_id,
            display_phone_number=data.display_phone_number,
        )
        db.add(mapping)

    db.commit()
    db.refresh(mapping)

    logger.info("WhatsApp number mapped (restaurant_id=%s)", restaurant_id)
    return mapping


@router.delete("/restaurants/{restaurant_id}/whatsapp-number")
def delete_whatsapp_number(restaurant_id: int, db: Session = Depends(get_db)):
    _get_restaurant_or_404(db, restaurant_id)

    mapping = (
        db.query(models.WhatsAppNumber)
        .filter(models.WhatsAppNumber.restaurant_id == restaurant_id)
        .first()
    )
    if mapping:
        db.delete(mapping)
        db.commit()
        logger.info("WhatsApp number unmapped (restaurant_id=%s)", restaurant_id)
        return {"message": "WhatsApp number mapping removed"}

    return {"message": "No WhatsApp number was mapped"}


# =========================================================
# CREATE ADMIN USER (issues a scoped key, shown once)
# =========================================================

@router.post("/admin-users", response_model=schemas.AdminUserCreateOut, status_code=201)
def create_admin_user(data: schemas.AdminUserCreate, db: Session = Depends(get_db)):
    # Validate every requested restaurant_id exists before creating
    # anything, so we never partially create an admin user with a
    # dangling/nonexistent grant.
    for restaurant_id in data.restaurant_ids:
        _get_restaurant_or_404(db, restaurant_id)

    plaintext_key, key_id, key_hash = generate_key()

    admin_user = models.AdminUser(key_id=key_id, key_hash=key_hash, label=data.label)
    db.add(admin_user)
    db.flush()  # assign admin_user.id without committing yet

    for restaurant_id in set(data.restaurant_ids):
        db.add(models.AdminRestaurantAccess(admin_user_id=admin_user.id, restaurant_id=restaurant_id))

    db.commit()
    db.refresh(admin_user)

    logger.info(
        "Admin user created (id=%s key_id=%s restaurant_ids=%s)",
        admin_user.id,
        key_id,
        sorted(set(data.restaurant_ids)),
    )

    return {**_admin_user_out(admin_user), "api_key": plaintext_key}


# =========================================================
# LIST / GET ADMIN USERS (never returns key material)
# =========================================================

@router.get("/admin-users", response_model=list[schemas.AdminUserOut])
def list_admin_users(db: Session = Depends(get_db)):
    admin_users = db.query(models.AdminUser).order_by(models.AdminUser.id).all()
    return [_admin_user_out(a) for a in admin_users]


@router.get("/admin-users/{admin_user_id}", response_model=schemas.AdminUserOut)
def get_admin_user(admin_user_id: int, db: Session = Depends(get_db)):
    admin_user = _get_admin_user_or_404(db, admin_user_id)
    return _admin_user_out(admin_user)


# =========================================================
# UPDATE ADMIN USER (deactivate/reactivate, relabel)
# =========================================================

@router.patch("/admin-users/{admin_user_id}", response_model=schemas.AdminUserOut)
def update_admin_user(
    admin_user_id: int, data: schemas.AdminUserUpdate, db: Session = Depends(get_db)
):
    admin_user = _get_admin_user_or_404(db, admin_user_id)

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(admin_user, field, value)

    db.commit()
    db.refresh(admin_user)

    logger.info(
        "Admin user updated (id=%s is_active=%s)", admin_user.id, admin_user.is_active
    )

    return _admin_user_out(admin_user)


# =========================================================
# ROTATE ADMIN USER'S KEY (immediate — old key stops working right away)
# =========================================================

@router.post("/admin-users/{admin_user_id}/rotate-key", response_model=schemas.AdminUserCreateOut)
def rotate_admin_user_key(admin_user_id: int, db: Session = Depends(get_db)):
    admin_user = _get_admin_user_or_404(db, admin_user_id)

    plaintext_key, key_id, key_hash = generate_key()
    admin_user.key_id = key_id
    admin_user.key_hash = key_hash

    db.commit()
    db.refresh(admin_user)

    logger.info("Admin user key rotated (id=%s new_key_id=%s)", admin_user.id, key_id)

    return {**_admin_user_out(admin_user), "api_key": plaintext_key}


# =========================================================
# GRANT / REVOKE A RESTAURANT SCOPE
# =========================================================

@router.post("/admin-users/{admin_user_id}/restaurants/{restaurant_id}", response_model=schemas.AdminUserOut)
def grant_restaurant_access(admin_user_id: int, restaurant_id: int, db: Session = Depends(get_db)):
    admin_user = _get_admin_user_or_404(db, admin_user_id)
    _get_restaurant_or_404(db, restaurant_id)

    existing = (
        db.query(models.AdminRestaurantAccess)
        .filter(
            models.AdminRestaurantAccess.admin_user_id == admin_user_id,
            models.AdminRestaurantAccess.restaurant_id == restaurant_id,
        )
        .first()
    )
    if not existing:
        db.add(models.AdminRestaurantAccess(admin_user_id=admin_user_id, restaurant_id=restaurant_id))
        db.commit()
        db.refresh(admin_user)
        logger.info(
            "Restaurant access granted (admin_user_id=%s restaurant_id=%s)",
            admin_user_id,
            restaurant_id,
        )

    return _admin_user_out(admin_user)


@router.delete("/admin-users/{admin_user_id}/restaurants/{restaurant_id}", response_model=schemas.AdminUserOut)
def revoke_restaurant_access(admin_user_id: int, restaurant_id: int, db: Session = Depends(get_db)):
    admin_user = _get_admin_user_or_404(db, admin_user_id)

    access = (
        db.query(models.AdminRestaurantAccess)
        .filter(
            models.AdminRestaurantAccess.admin_user_id == admin_user_id,
            models.AdminRestaurantAccess.restaurant_id == restaurant_id,
        )
        .first()
    )
    if access:
        db.delete(access)
        db.commit()
        db.refresh(admin_user)
        logger.info(
            "Restaurant access revoked (admin_user_id=%s restaurant_id=%s)",
            admin_user_id,
            restaurant_id,
        )

    return _admin_user_out(admin_user)


# =========================================================
# SUBSCRIPTION MANAGEMENT (Jantar SaaS Phase 2 — manual only; no
# payment provider, checkout, or enforcement yet — see app/plans.py
# and app/models.py:Subscription)
# =========================================================

def _get_subscription_or_404(db: Session, restaurant_id: int) -> models.Subscription:
    subscription = (
        db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id == restaurant_id)
        .first()
    )
    if not subscription:
        raise HTTPException(status_code=404, detail="No subscription exists for this restaurant yet")
    return subscription


@router.get(
    "/restaurants/{restaurant_id}/subscription",
    response_model=schemas.SubscriptionAdminOut,
)
def get_restaurant_subscription_as_superadmin(restaurant_id: int, db: Session = Depends(get_db)):
    _get_restaurant_or_404(db, restaurant_id)
    return _get_subscription_or_404(db, restaurant_id)


@router.patch(
    "/restaurants/{restaurant_id}/subscription",
    response_model=schemas.SubscriptionAdminOut,
)
def update_restaurant_subscription(
    restaurant_id: int, data: schemas.SubscriptionAdminUpdate, db: Session = Depends(get_db)
):
    """
    Manual plan/status changes only, for as long as no payment provider
    is wired — plan_code and status are the only fields exposed here
    (validated against the fixed set of plans/lifecycle states by
    schemas.SubscriptionAdminUpdate's own typing). Provider-linked
    fields (billing_provider, provider_customer_id,
    provider_subscription_id, current_period_end) are deliberately not
    editable through this endpoint; those are for a future billing
    integration to set from real provider data, not for manual entry.
    """
    _get_restaurant_or_404(db, restaurant_id)
    subscription = _get_subscription_or_404(db, restaurant_id)

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(subscription, field, value)

    db.commit()
    db.refresh(subscription)

    logger.info(
        "Subscription updated (restaurant_id=%s plan_code=%s status=%s)",
        restaurant_id, subscription.plan_code, subscription.status,
    )
    return subscription
