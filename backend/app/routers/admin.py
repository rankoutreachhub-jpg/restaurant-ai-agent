from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas


router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
)


# =========================================================
# GET RESTAURANT
# =========================================================

@router.get("/restaurant/{restaurant_id}")
def get_restaurant(
    restaurant_id: int,
    db: Session = Depends(get_db),
):
    restaurant = db.query(models.Restaurant).filter(
        models.Restaurant.id == restaurant_id
    ).first()

    if not restaurant:
        raise HTTPException(
            status_code=404,
            detail="Restaurant not found"
        )

    return {
        "id": restaurant.id,
        "name": restaurant.name,
        "address": restaurant.address,
        "phone": restaurant.phone,
        "email": restaurant.email,
        "map_link": restaurant.map_link,
        "parking_notes": restaurant.parking_notes,
    }


# =========================================================
# UPDATE RESTAURANT
# =========================================================

@router.patch("/restaurant/{restaurant_id}")
def update_restaurant(
    restaurant_id: int,
    data: schemas.RestaurantUpdate,
    db: Session = Depends(get_db),
):
    restaurant = db.query(models.Restaurant).filter(
        models.Restaurant.id == restaurant_id
    ).first()

    if not restaurant:
        raise HTTPException(
            status_code=404,
            detail="Restaurant not found"
        )

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
        },
    }


# =========================================================
# GET MENU
# =========================================================

@router.get("/restaurant/{restaurant_id}/menu")
def get_menu(
    restaurant_id: int,
    db: Session = Depends(get_db),
):
    restaurant = db.query(models.Restaurant).filter(
        models.Restaurant.id == restaurant_id
    ).first()

    if not restaurant:
        raise HTTPException(
            status_code=404,
            detail="Restaurant not found"
        )

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
):
    restaurant = db.query(models.Restaurant).filter(
        models.Restaurant.id == restaurant_id
    ).first()

    if not restaurant:
        raise HTTPException(
            status_code=404,
            detail="Restaurant not found"
        )

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
):
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
):
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
):
    restaurant = db.query(models.Restaurant).filter(
        models.Restaurant.id == restaurant_id
    ).first()

    if not restaurant:
        raise HTTPException(
            status_code=404,
            detail="Restaurant not found"
        )

    opening_hours = db.query(models.OpeningHours).filter(
        models.OpeningHours.restaurant_id == restaurant_id
    ).all()

    return opening_hours


# =========================================================
# UPDATE OPENING HOURS
# =========================================================

@router.patch("/restaurant/{restaurant_id}/opening-hours/{day_of_week}")
def update_opening_hours(
    restaurant_id: int,
    day_of_week: str,
    data: schemas.OpeningHoursUpdate,
    db: Session = Depends(get_db),
):
    restaurant = db.query(models.Restaurant).filter(
        models.Restaurant.id == restaurant_id
    ).first()

    if not restaurant:
        raise HTTPException(
            status_code=404,
            detail="Restaurant not found"
        )

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