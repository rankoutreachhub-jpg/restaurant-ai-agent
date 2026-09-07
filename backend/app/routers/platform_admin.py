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
from ..rate_limit import admin_rate_limiter

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

@router.get("/restaurants", response_model=list[schemas.RestaurantOut])
def list_restaurants(db: Session = Depends(get_db)):
    return db.query(models.Restaurant).order_by(models.Restaurant.id).all()


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
