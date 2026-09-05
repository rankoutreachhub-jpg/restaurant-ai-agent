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

from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, Text
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

    opening_hours = relationship("OpeningHours", back_populates="restaurant")
    menu_items = relationship("MenuItem", back_populates="restaurant")
    faqs = relationship("FAQ", back_populates="restaurant")


class OpeningHours(Base):
    __tablename__ = "opening_hours"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    day_of_week = Column(String, nullable=False)  # e.g. "Monday"
    open_time = Column(String, nullable=True)     # e.g. "12:00" (null if closed)
    close_time = Column(String, nullable=True)    # e.g. "22:00" (null if closed)
    is_closed = Column(Boolean, default=False)

    restaurant = relationship("Restaurant", back_populates="opening_hours")


class MenuItem(Base):
    __tablename__ = "menu_items"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    category = Column(String, nullable=False)      # e.g. "Starters"
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=False)
    dietary_tags = Column(String, nullable=True)   # e.g. "vegetarian, gluten-free"

    restaurant = relationship("Restaurant", back_populates="menu_items")


class FAQ(Base):
    __tablename__ = "faqs"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False)
    question = Column(String, nullable=False)
    answer = Column(Text, nullable=False)

    restaurant = relationship("Restaurant", back_populates="faqs")
