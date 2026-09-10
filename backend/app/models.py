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

from sqlalchemy import Column, Index, Integer, String, Float, Boolean, ForeignKey, Text, Date, Time, DateTime, UniqueConstraint
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
    # One row per (restaurant, day) — enforced at the DB level (Stage 3
    # Step 4) as a backstop against a check-then-insert race in the
    # opening-hours creation endpoint (see routers/admin.py), not just an
    # application-level check.
    __table_args__ = (UniqueConstraint("restaurant_id", "day_of_week", name="uq_opening_hours_restaurant_day"),)

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
    # Set once the booking-confirmation email has actually been sent
    # (see app/booking_notifications.py) — NULL means "not sent yet",
    # which is also the correct meaning for every pre-existing row.
    # Doubles as the idempotency guard preventing a duplicate send.
    confirmation_sent_at = Column(DateTime, nullable=True)

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


class Conversation(Base):
    """
    A persisted /chat conversation (Stage 3 Step 5; Stage 3 Step 6B adds
    the WhatsApp channel). Two identifiers, two trust boundaries:
      - `id` (the normal integer PK) is used internally and by the
        authenticated admin surface (routers/conversations.py), exactly
        like every other resource's id.
      - `public_token` is the ONLY identifier ever exposed to or
        accepted from the unauthenticated /chat endpoint. It is not an
        authentication mechanism — it identifies no user and grants no
        admin privilege — but it must be unguessable (see
        app/conversations.py's generation, same style as
        app/admin_keys.py) because on a public, unauthenticated surface
        a small sequential integer would let anyone enumerate other
        customers' conversations by incrementing a request field. A
        WhatsApp conversation still gets one (every other admin/
        persistence code path already assumes one exists), but it is
        never handed to the WhatsApp customer — that channel's identity
        is external_id, not public_token.

    No status/lifecycle field: nothing in the current stateless
    request/response chat flow can detect "this conversation is over,"
    so adding one now would have zero consumers. `channel` ("web" or,
    since Stage 3 Step 6B, "whatsapp") is the one deliberate forward-
    looking column from Step 5, seeding the WhatsApp integration without
    a later backfill-guess migration.

    `external_id` (Stage 3 Step 6B) is the channel's own identifier for
    who this conversation is with — for WhatsApp, the customer's E.164
    phone number (see app/phone.py) — nullable because "web" conversations
    have no such external identity. The composite index below is what
    app/conversations.get_or_create_whatsapp_conversation() queries to
    resolve "does this restaurant already have a recent conversation with
    this WhatsApp customer".
    """
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_restaurant_id_updated_at", "restaurant_id", "updated_at"),
        Index(
            "ix_conversations_restaurant_channel_external",
            "restaurant_id", "channel", "external_id",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    public_token = Column(String, unique=True, nullable=False, index=True)
    channel = Column(String, nullable=False, default="web")
    external_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    restaurant = relationship("Restaurant")
    messages = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    """
    One turn of a persisted conversation. Only "user" and "assistant"
    roles are ever stored (enforced in schemas.py, matching the
    project's existing convention of Literal-enforcing role at the
    application layer rather than a DB CHECK constraint — see
    schemas.ChatMessage). The system/context block is deliberately
    NEVER persisted here: knowledge.build_restaurant_context() is
    rebuilt fresh from live data on every /chat call, so replaying a
    stored copy would silently go stale the moment menu/hours/FAQs
    change — exactly what this project's "never invent facts" policy
    exists to prevent.

    Write-once: a Message is never edited after creation, so unlike
    Conversation it needs no updated_at.

    `external_message_id` (Stage 3 Step 6B) holds Meta's own WhatsApp
    message id (the incoming customer message only — there is no
    equivalent id for our own outbound reply). It is nullable (web chat
    messages have none) but globally unique when present: Meta can and
    does redeliver webhook events, and this is what lets
    app/whatsapp_processing.py detect "we've already handled this exact
    message" before ever calling Gemini or attempting a booking again.
    """
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    triggered_tool_call = Column(Boolean, nullable=False, default=False)
    external_message_id = Column(String, unique=True, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class WhatsAppNumber(Base):
    """
    Maps one Meta WhatsApp Business `phone_number_id` to one restaurant
    (Stage 3 Step 6B). v1 architecture: exactly one WhatsApp number per
    restaurant (uq restaurant_id below), and phone_number_id is globally
    unique — the same number can't be mapped to two restaurants.

    This is the ONLY way a webhook payload's phone_number_id is ever
    turned into a restaurant_id (see app/whatsapp_processing.py); an
    unrecognised phone_number_id resolves to no restaurant and the
    message is dropped, never defaulted to another restaurant.

    Deliberately no per-restaurant access token here — v1 uses one
    platform-wide Meta app and WHATSAPP_ACCESS_TOKEN (app/config.py) for
    every restaurant's number, so there is nothing sensitive to protect
    on this row beyond the identifiers themselves.
    """
    __tablename__ = "whatsapp_numbers"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, unique=True, index=True)
    phone_number_id = Column(String, nullable=False, unique=True, index=True)
    display_phone_number = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant")


class WidgetConfig(Base):
    """
    Public-facing widget branding/config for a restaurant (Stage 4 Phase
    A — Production Customer Widget). One per restaurant (v1) — same
    "one per restaurant" shape as WhatsAppNumber above.

    `widget_key` (see app/widget_keys.py) is the only identifier a
    future customer-facing widget will ever carry — it is public (safe
    to sit in plain HTML), high-entropy, and non-sequential, but it is
    NOT a secret and grants no admin privilege: it only ever resolves to
    this one restaurant's public branding/config, never to anything
    behind X-Admin-API-Key. This phase only adds the table and
    restaurant-scoped admin management of it — nothing yet reads
    widget_key from an unauthenticated request (that's a later, separate
    phase: a public GET /widget/{widget_key}/config endpoint).

    All branding fields are nullable/defaulted — a restaurant can have a
    row here with nothing customized yet, and the eventual public
    surface falls back to sensible generated defaults (e.g. a welcome
    message built from the restaurant's own name) rather than requiring
    every field to be filled in before the widget can be used at all.
    """
    __tablename__ = "widget_configs"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, unique=True, index=True)
    widget_key = Column(String, nullable=False, unique=True, index=True)
    welcome_message = Column(Text, nullable=True)
    primary_language = Column(String, nullable=False, default="en-GB")
    logo_url = Column(String, nullable=True)
    accent_color = Column(String, nullable=True)
    booking_enabled = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    restaurant = relationship("Restaurant")
    allowed_origins = relationship(
        "WidgetAllowedOrigin", back_populates="widget_config", cascade="all, delete-orphan",
        order_by="WidgetAllowedOrigin.created_at",
    )


class WidgetAllowedOrigin(Base):
    """
    One browser origin a restaurant's widget may be embedded on (Stage 4
    Phase D — strict per-restaurant CORS). A WidgetConfig may have zero
    or more of these; zero means "not yet locked down" — see
    app/widget_cors.py for what that means for a real browser request
    (fail closed, not fail open).

    `origin` is stored EXACTLY as a browser's Origin header would send
    it — "scheme://host[:port]", lowercased, no path/query/fragment/
    trailing slash — validated at write time (see
    schemas.WidgetAllowedOriginCreate) so this table can never contain a
    value that wouldn't exact-match a real request. No wildcard
    subdomains in v1: "https://example.com" and "https://www.example.com"
    are two separate rows, not one pattern.

    The unique constraint is scoped to (widget_config_id, origin), not
    origin alone — the same literal origin (e.g. a shared local-dev
    address like "http://127.0.0.1:5500") can legitimately be registered
    by multiple different restaurants without conflict.
    """
    __tablename__ = "widget_allowed_origins"
    __table_args__ = (
        UniqueConstraint("widget_config_id", "origin", name="uq_widget_allowed_origins_config_origin"),
    )

    id = Column(Integer, primary_key=True, index=True)
    widget_config_id = Column(Integer, ForeignKey("widget_configs.id"), nullable=False, index=True)
    origin = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    widget_config = relationship("WidgetConfig", back_populates="allowed_origins")
