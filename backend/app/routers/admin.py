import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..auth import AdminIdentity, get_current_admin
from ..authz import require_restaurant_access
from ..rate_limit import admin_rate_limiter
from ..widget_keys import generate_widget_key

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    # Rate limiting is applied to every route on this router, current
    # and future, so brute-forcing/flooding the admin API is throttled
    # before the key check even runs. Authentication (get_current_admin)
    # and restaurant-scope authorization (require_restaurant_access) are
    # each declared as a parameter on every handler below instead of
    # here, because a handler needs the resolved AdminIdentity itself to
    # check which restaurant_id(s) it may touch — not just the fact that
    # *some* valid key was presented.
    dependencies=[Depends(admin_rate_limiter)],
)


# =========================================================
# WHOAMI
# =========================================================
# Stage 3 Step 6A: lets an admin-facing client (the dashboard) discover
# its own scope without guessing — never anyone else's. Superadmin gets
# restaurant_ids=None (meaning "every restaurant"; the dashboard then
# calls GET /admin/platform/restaurants for the actual list). A scoped
# admin gets exactly its own granted restaurant_ids, the same set
# require_restaurant_access already enforces on every other route.

@router.get("/me", response_model=schemas.AdminMeOut)
def get_current_admin_identity(current_admin: AdminIdentity = Depends(get_current_admin)):
    return schemas.AdminMeOut(
        is_superadmin=current_admin.is_superadmin,
        restaurant_ids=(
            None if current_admin.is_superadmin
            else sorted(current_admin.allowed_restaurant_ids or [])
        ),
    )


# =========================================================
# GET RESTAURANT
# =========================================================

@router.get("/restaurant/{restaurant_id}")
def get_restaurant(
    restaurant_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    restaurant = require_restaurant_access(restaurant_id, db, current_admin)

    return {
        "id": restaurant.id,
        "name": restaurant.name,
        "address": restaurant.address,
        "phone": restaurant.phone,
        "email": restaurant.email,
        "map_link": restaurant.map_link,
        "parking_notes": restaurant.parking_notes,
        "seating_capacity": restaurant.seating_capacity,
    }


# =========================================================
# UPDATE RESTAURANT
# =========================================================

@router.patch("/restaurant/{restaurant_id}")
def update_restaurant(
    restaurant_id: int,
    data: schemas.RestaurantUpdate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    restaurant = require_restaurant_access(restaurant_id, db, current_admin)

    updates = data.model_dump(exclude_unset=True)

    for field, value in updates.items():
        setattr(restaurant, field, value)

    db.commit()
    db.refresh(restaurant)

    return {
        "message": "Restaurant updated successfully",
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
# GET MENU
# =========================================================

@router.get("/restaurant/{restaurant_id}/menu")
def get_menu(
    restaurant_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    menu_items = db.query(models.MenuItem).filter(
        models.MenuItem.restaurant_id == restaurant_id
    ).all()

    return menu_items


# =========================================================
# CREATE MENU ITEM
# =========================================================

@router.post("/restaurant/{restaurant_id}/menu")
def create_menu_item(
    restaurant_id: int,
    data: schemas.MenuItemCreate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    menu_item = models.MenuItem(
        restaurant_id=restaurant_id,
        category=data.category,
        name=data.name,
        description=data.description,
        price=data.price,
        dietary_tags=data.dietary_tags,
    )

    db.add(menu_item)
    db.commit()
    db.refresh(menu_item)

    return {
        "message": "Menu item created successfully",
        "menu_item": {
            "id": menu_item.id,
            "restaurant_id": menu_item.restaurant_id,
            "category": menu_item.category,
            "name": menu_item.name,
            "description": menu_item.description,
            "price": menu_item.price,
            "dietary_tags": menu_item.dietary_tags,
        },
    }


# =========================================================
# UPDATE MENU ITEM
# =========================================================

@router.patch("/restaurant/{restaurant_id}/menu/{menu_item_id}")
def update_menu_item(
    restaurant_id: int,
    menu_item_id: int,
    data: schemas.MenuItemUpdate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    menu_item = db.query(models.MenuItem).filter(
        models.MenuItem.id == menu_item_id,
        models.MenuItem.restaurant_id == restaurant_id,
    ).first()

    if not menu_item:
        raise HTTPException(
            status_code=404,
            detail="Menu item not found"
        )

    updates = data.model_dump(exclude_unset=True)

    for field, value in updates.items():
        setattr(menu_item, field, value)

    db.commit()
    db.refresh(menu_item)

    return {
        "message": "Menu item updated successfully",
        "menu_item": {
            "id": menu_item.id,
            "restaurant_id": menu_item.restaurant_id,
            "category": menu_item.category,
            "name": menu_item.name,
            "description": menu_item.description,
            "price": menu_item.price,
            "dietary_tags": menu_item.dietary_tags,
        },
    }


# =========================================================
# DELETE MENU ITEM
# =========================================================

@router.delete("/restaurant/{restaurant_id}/menu/{menu_item_id}")
def delete_menu_item(
    restaurant_id: int,
    menu_item_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    menu_item = db.query(models.MenuItem).filter(
        models.MenuItem.id == menu_item_id,
        models.MenuItem.restaurant_id == restaurant_id,
    ).first()

    if not menu_item:
        raise HTTPException(
            status_code=404,
            detail="Menu item not found"
        )

    db.delete(menu_item)
    db.commit()

    return {
        "message": "Menu item deleted successfully",
        "menu_item_id": menu_item_id,
    }


# =========================================================
# GET OPENING HOURS
# =========================================================

@router.get("/restaurant/{restaurant_id}/opening-hours")
def get_opening_hours(
    restaurant_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    opening_hours = db.query(models.OpeningHours).filter(
        models.OpeningHours.restaurant_id == restaurant_id
    ).all()

    return opening_hours


# =========================================================
# CREATE OPENING HOURS
# =========================================================
# Stage 3 Step 4: a newly onboarded restaurant starts with zero
# OpeningHours rows, and the UPDATE endpoint below only ever modifies an
# existing day — this is the only way to create the initial 7 (or any
# subset of them; the request accepts 1-7 days so they can also be
# added one at a time). Rejects with 409 if any requested day already
# exists for this restaurant, rather than silently skipping or
# overwriting it — updating an existing day is what PATCH is for.

@router.post("/restaurant/{restaurant_id}/opening-hours", status_code=201)
def create_opening_hours(
    restaurant_id: int,
    data: schemas.OpeningHoursCreate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    requested_days = [item.day_of_week for item in data.days]
    existing_days = {
        row.day_of_week
        for row in db.query(models.OpeningHours.day_of_week)
        .filter(
            models.OpeningHours.restaurant_id == restaurant_id,
            models.OpeningHours.day_of_week.in_(requested_days),
        )
        .all()
    }
    if existing_days:
        raise HTTPException(
            status_code=409,
            detail=f"Opening hours already exist for: {', '.join(sorted(existing_days))}",
        )

    new_rows = [
        models.OpeningHours(
            restaurant_id=restaurant_id,
            day_of_week=item.day_of_week,
            open_time=item.open_time,
            close_time=item.close_time,
            is_closed=item.is_closed,
        )
        for item in data.days
    ]
    db.add_all(new_rows)
    try:
        db.commit()
    except IntegrityError:
        # Backstop for a concurrent request creating the same day(s)
        # between the check above and this commit — the unique
        # constraint on (restaurant_id, day_of_week) is what actually
        # prevents the duplicate; this just turns it into the same 409
        # instead of a raw 500.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Opening hours for one or more requested days already exist.",
        )

    for row in new_rows:
        db.refresh(row)

    return {
        "message": "Opening hours created successfully",
        "opening_hours": [
            {
                "id": row.id,
                "restaurant_id": row.restaurant_id,
                "day_of_week": row.day_of_week,
                "open_time": row.open_time,
                "close_time": row.close_time,
                "is_closed": row.is_closed,
            }
            for row in new_rows
        ],
    }


# =========================================================
# UPDATE OPENING HOURS
# =========================================================

@router.patch("/restaurant/{restaurant_id}/opening-hours/{day_of_week}")
def update_opening_hours(
    restaurant_id: int,
    day_of_week: str,
    data: schemas.OpeningHoursUpdate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    opening_hours = db.query(models.OpeningHours).filter(
        models.OpeningHours.restaurant_id == restaurant_id,
        models.OpeningHours.day_of_week == day_of_week,
    ).first()

    if not opening_hours:
        raise HTTPException(
            status_code=404,
            detail="Opening hours for this day not found"
        )

    updates = data.model_dump(exclude_unset=True)

    for field, value in updates.items():
        setattr(opening_hours, field, value)

    db.commit()
    db.refresh(opening_hours)

    return {
        "message": "Opening hours updated successfully",
        "opening_hours": {
            "id": opening_hours.id,
            "restaurant_id": opening_hours.restaurant_id,
            "day_of_week": opening_hours.day_of_week,
            "open_time": opening_hours.open_time,
            "close_time": opening_hours.close_time,
            "is_closed": opening_hours.is_closed,
        },
    }


def _get_faq_or_404(db: Session, restaurant_id: int, faq_id: int) -> models.FAQ:
    faq = (
        db.query(models.FAQ)
        .filter(models.FAQ.id == faq_id, models.FAQ.restaurant_id == restaurant_id)
        .first()
    )
    if not faq:
        raise HTTPException(status_code=404, detail="FAQ not found")
    return faq


# =========================================================
# GET FAQS
# =========================================================

@router.get("/restaurant/{restaurant_id}/faqs")
def get_faqs(
    restaurant_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    faqs = db.query(models.FAQ).filter(models.FAQ.restaurant_id == restaurant_id).all()

    return faqs


# =========================================================
# CREATE FAQ
# =========================================================

@router.post("/restaurant/{restaurant_id}/faqs", status_code=201)
def create_faq(
    restaurant_id: int,
    data: schemas.FAQCreate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    faq = models.FAQ(restaurant_id=restaurant_id, question=data.question, answer=data.answer)

    db.add(faq)
    db.commit()
    db.refresh(faq)

    return {
        "message": "FAQ created successfully",
        "faq": {
            "id": faq.id,
            "restaurant_id": faq.restaurant_id,
            "question": faq.question,
            "answer": faq.answer,
        },
    }


# =========================================================
# UPDATE FAQ
# =========================================================

@router.patch("/restaurant/{restaurant_id}/faqs/{faq_id}")
def update_faq(
    restaurant_id: int,
    faq_id: int,
    data: schemas.FAQUpdate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)
    faq = _get_faq_or_404(db, restaurant_id, faq_id)

    updates = data.model_dump(exclude_unset=True)

    for field, value in updates.items():
        setattr(faq, field, value)

    db.commit()
    db.refresh(faq)

    return {
        "message": "FAQ updated successfully",
        "faq": {
            "id": faq.id,
            "restaurant_id": faq.restaurant_id,
            "question": faq.question,
            "answer": faq.answer,
        },
    }


# =========================================================
# DELETE FAQ
# =========================================================

@router.delete("/restaurant/{restaurant_id}/faqs/{faq_id}")
def delete_faq(
    restaurant_id: int,
    faq_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)
    faq = _get_faq_or_404(db, restaurant_id, faq_id)

    db.delete(faq)
    db.commit()

    return {
        "message": "FAQ deleted successfully",
        "faq_id": faq_id,
    }


# =========================================================
# WIDGET CONFIG (Stage 4 Phase A: Production Customer Widget)
# =========================================================
# Restaurant-scoped, not superadmin-only — unlike WhatsApp number
# mapping (a platform-gatekept concern), a restaurant's own widget
# branding/welcome-message is exactly the kind of day-to-day thing its
# own admin should self-serve. Only the admin-facing shape lives here in
# this phase: nothing yet reads widget_key from an unauthenticated
# request (see app/models.py:WidgetConfig's docstring).

@router.get("/restaurant/{restaurant_id}/widget-config", response_model=schemas.WidgetConfigOut)
def get_widget_config(
    restaurant_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    config = (
        db.query(models.WidgetConfig)
        .filter(models.WidgetConfig.restaurant_id == restaurant_id)
        .first()
    )
    if not config:
        raise HTTPException(status_code=404, detail="No widget config exists for this restaurant yet")

    return config


@router.post(
    "/restaurant/{restaurant_id}/widget-config",
    response_model=schemas.WidgetConfigOut,
    status_code=201,
)
def upsert_widget_config(
    restaurant_id: int,
    data: schemas.WidgetConfigUpdate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    """
    Creates the restaurant's one widget config row if none exists yet,
    or updates it in place if one does (an upsert — there's only ever
    one, same shape as platform_admin.py's WhatsApp number mapping).
    widget_key is generated once, here, on first creation only — it is
    never accepted as input and never regenerated by this endpoint.
    """
    require_restaurant_access(restaurant_id, db, current_admin)

    updates = data.model_dump(exclude_unset=True)

    config = (
        db.query(models.WidgetConfig)
        .filter(models.WidgetConfig.restaurant_id == restaurant_id)
        .first()
    )
    if config:
        for field, value in updates.items():
            setattr(config, field, value)
    else:
        config = models.WidgetConfig(
            restaurant_id=restaurant_id,
            widget_key=generate_widget_key(),
            **updates,
        )
        db.add(config)

    db.commit()
    db.refresh(config)

    logger.info("Widget config upserted (restaurant_id=%s)", restaurant_id)
    return config


def _get_widget_config_or_404(db: Session, restaurant_id: int) -> models.WidgetConfig:
    config = (
        db.query(models.WidgetConfig)
        .filter(models.WidgetConfig.restaurant_id == restaurant_id)
        .first()
    )
    if not config:
        raise HTTPException(status_code=404, detail="No widget config exists for this restaurant yet")
    return config


# =========================================================
# WIDGET ALLOWED ORIGINS (Stage 4 Phase D: strict per-restaurant CORS)
# =========================================================
# A restaurant's widget may have zero or more allowed browser origins —
# see app/models.py:WidgetAllowedOrigin and app/widget_cors.py for how
# these are actually enforced on the public /widget/* surface. Exact
# origins only, no wildcard subdomains in v1.

@router.post(
    "/restaurant/{restaurant_id}/widget-config/origins",
    response_model=schemas.WidgetAllowedOriginOut,
    status_code=201,
)
def add_widget_allowed_origin(
    restaurant_id: int,
    data: schemas.WidgetAllowedOriginCreate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)
    config = _get_widget_config_or_404(db, restaurant_id)

    existing = (
        db.query(models.WidgetAllowedOrigin)
        .filter(
            models.WidgetAllowedOrigin.widget_config_id == config.id,
            models.WidgetAllowedOrigin.origin == data.origin,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="This origin is already allowed for this widget.")

    origin_row = models.WidgetAllowedOrigin(widget_config_id=config.id, origin=data.origin)
    db.add(origin_row)
    try:
        db.commit()
    except IntegrityError:
        # Backstop for a concurrent request registering the same origin
        # between the check above and this commit — the unique
        # constraint on (widget_config_id, origin) is what actually
        # prevents the duplicate; this just turns it into the same 409
        # instead of a raw 500.
        db.rollback()
        raise HTTPException(status_code=409, detail="This origin is already allowed for this widget.")
    db.refresh(origin_row)

    logger.info("Widget allowed origin added (restaurant_id=%s)", restaurant_id)
    return origin_row


@router.delete("/restaurant/{restaurant_id}/widget-config/origins/{origin_id}")
def delete_widget_allowed_origin(
    restaurant_id: int,
    origin_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)
    config = _get_widget_config_or_404(db, restaurant_id)

    # Scoped to THIS restaurant's own widget_config_id — an origin_id
    # belonging to a different restaurant's widget never matches here,
    # regardless of the caller's own authorization, so it 404s exactly
    # like a nonexistent id would rather than ever deleting someone
    # else's row.
    origin_row = (
        db.query(models.WidgetAllowedOrigin)
        .filter(
            models.WidgetAllowedOrigin.id == origin_id,
            models.WidgetAllowedOrigin.widget_config_id == config.id,
        )
        .first()
    )
    if not origin_row:
        raise HTTPException(status_code=404, detail="Allowed origin not found")

    db.delete(origin_row)
    db.commit()

    logger.info("Widget allowed origin removed (restaurant_id=%s)", restaurant_id)
    return {"message": "Allowed origin removed", "origin_id": origin_id}
