"""
Admin booking management endpoints.

Kept as its own router (rather than folded into routers/admin.py, which
is already sizeable) but protected identically: the same admin rate
limiter, authentication (get_current_admin), and restaurant-scope
authorization (require_restaurant_access) apply to booking management
exactly as they do to every other /admin/* route — see routers/admin.py
for why those are declared per-handler rather than at router level.
"""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import AdminIdentity, get_current_admin
from ..authz import require_restaurant_access
from ..booking import BookingConflictError, create_booking, update_booking
from ..database import get_db
from ..rate_limit import admin_rate_limiter

router = APIRouter(
    prefix="/admin",
    tags=["Admin - Bookings"],
    dependencies=[Depends(admin_rate_limiter)],
)


def _get_booking_or_404(db: Session, restaurant_id: int, booking_id: int) -> models.Booking:
    booking = (
        db.query(models.Booking)
        .filter(models.Booking.id == booking_id, models.Booking.restaurant_id == restaurant_id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking


# =========================================================
# LIST BOOKINGS
# =========================================================

@router.get("/restaurant/{restaurant_id}/bookings", response_model=List[schemas.BookingOut])
def list_bookings(
    restaurant_id: int,
    status_filter: Optional[schemas.BookingStatus] = Query(default=None, alias="status"),
    booking_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    query = db.query(models.Booking).filter(models.Booking.restaurant_id == restaurant_id)
    if status_filter:
        query = query.filter(models.Booking.status == status_filter)
    if booking_date:
        query = query.filter(models.Booking.booking_date == booking_date)

    return query.order_by(models.Booking.booking_date, models.Booking.booking_time).all()


# =========================================================
# CREATE BOOKING
# =========================================================

@router.post(
    "/restaurant/{restaurant_id}/bookings",
    response_model=schemas.BookingOut,
    status_code=status.HTTP_201_CREATED,
)
def create_booking_endpoint(
    restaurant_id: int,
    data: schemas.BookingCreate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    restaurant = require_restaurant_access(restaurant_id, db, current_admin)
    try:
        return create_booking(db, restaurant, data)
    except BookingConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


# =========================================================
# GET BOOKING
# =========================================================

@router.get("/restaurant/{restaurant_id}/bookings/{booking_id}", response_model=schemas.BookingOut)
def get_booking(
    restaurant_id: int,
    booking_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)
    return _get_booking_or_404(db, restaurant_id, booking_id)


# =========================================================
# UPDATE BOOKING
# =========================================================

@router.patch("/restaurant/{restaurant_id}/bookings/{booking_id}", response_model=schemas.BookingOut)
def update_booking_endpoint(
    restaurant_id: int,
    booking_id: int,
    data: schemas.BookingUpdate,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    restaurant = require_restaurant_access(restaurant_id, db, current_admin)
    booking = _get_booking_or_404(db, restaurant_id, booking_id)
    try:
        return update_booking(db, restaurant, booking, data)
    except BookingConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


# =========================================================
# DELETE BOOKING
# =========================================================

@router.delete("/restaurant/{restaurant_id}/bookings/{booking_id}")
def delete_booking(
    restaurant_id: int,
    booking_id: int,
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)
    booking = _get_booking_or_404(db, restaurant_id, booking_id)

    db.delete(booking)
    db.commit()

    return {"message": "Booking deleted successfully", "booking_id": booking_id}
