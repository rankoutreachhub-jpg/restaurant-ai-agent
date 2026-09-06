"""
Database models (tables).

IMPORTANT MULTI-TENANT DESIGN NOTE:
Even though Stage 1 only ever has ONE restaurant, every table below
(except Restaurant itself) includes a `restaurant_id` foreign key.
This means that later, when we support multiple restaurants, we do NOT
need to redesign the database — we just start inserting rows with
different restaurant_id values, and the existing queries already filter
by restaurant_id. This is the "future-proofing" the user asked for.
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, Text, Date, Time, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from .database import Base


class Restaurant(Base):
    __tablename__ = "restaurants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=False)
    map_link = Column(String, nullable=True)
    parking_notes = Column(String, nullable=True)
    # Total seats available at any one time — the basis for the
    # capacity-based overlap check in app/booking.py. Not per-table;
    # see that module's docstring for why.
    seating_capacity = Column(Integer, nullable=False, default=40)

    opening_hours = relationship("OpeningHours", back_populates="restaurant")
    menu_items = relationship("MenuItem", back_populates="restaurant")
    faqs = relationship("FAQ", back_populates="restaurant")
    bookings = relationship("Booking", back_populates="restaurant")


class OpeningHours(Base):
    __tablename__ = "opening_hours"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    day_of_week = Column(String, nullable=False)  # e.g. "Monday"
    open_time = Column(String, nullable=True)     # e.g. "12:00" (null if closed)
    close_time = Column(String, nullable=True)    # e.g. "22:00" (null if closed)
    is_closed = Column(Boolean, default=False)

    restaurant = relationship("Restaurant", back_populates="opening_hours")


class MenuItem(Base):
    __tablename__ = "menu_items"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    category = Column(String, nullable=False)      # e.g. "Starters"
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=False)
    dietary_tags = Column(String, nullable=True)   # e.g. "vegetarian, gluten-free"

    restaurant = relationship("Restaurant", back_populates="menu_items")


class FAQ(Base):
    __tablename__ = "faqs"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    question = Column(String, nullable=False)
    answer = Column(Text, nullable=False)

    restaurant = relationship("Restaurant", back_populates="faqs")


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    customer_name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=False)
    booking_date = Column(Date, nullable=False, index=True)
    booking_time = Column(Time, nullable=False)
    party_size = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="confirmed")  # "confirmed" | "cancelled"
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant", back_populates="bookings")


class AdminUser(Base):
    """
    A restaurant-scoped admin identity (Stage 3 Step 3 — multi-tenant
    authorization). This is separate from, and does not replace, the
    platform-superadmin ADMIN_API_KEY/ADMIN_API_KEY_PREVIOUS env vars
    (see app/auth.py) — those remain a config-only identity with implicit
    access to every restaurant, used for platform operations like
    onboarding a restaurant or issuing its first AdminUser. Rows here are
    for day-to-day admins scoped to one or more specific restaurants.

    Only a hash of the issued API key is ever stored (see
    app/admin_keys.py) — the plaintext is returned exactly once, at
    creation or rotation time, and is not recoverable afterwards.
    key_id is a public, non-secret lookup handle (indexed) so verifying
    a key doesn't require scanning and comparing against every row.
    """
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)
    key_id = Column(String, unique=True, nullable=False, index=True)
    key_hash = Column(String, nullable=False)
    label = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    restaurant_access = relationship(
        "AdminRestaurantAccess", back_populates="admin_user", cascade="all, delete-orphan"
    )


class AdminRestaurantAccess(Base):
    """One (admin_user, restaurant) grant. An AdminUser may have several
    of these (multi-restaurant admin); the unique constraint prevents
    duplicate grants of the same restaurant to the same admin user."""
    __tablename__ = "admin_restaurant_access"
    __table_args__ = (UniqueConstraint("admin_user_id", "restaurant_id", name="uq_admin_restaurant_access"),)

    id = Column(Integer, primary_key=True, index=True)
    admin_user_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    admin_user = relationship("AdminUser", back_populates="restaurant_access")
    restaurant = relationship("Restaurant")
