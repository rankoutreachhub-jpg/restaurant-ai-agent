"""
Database connection setup.

SQLite remains the zero-setup default for local dev (see config.py), but
DATABASE_URL can point at PostgreSQL instead — switching is just that one
env var; none of the model or query code needs to change. Schema is
managed by Alembic (see backend/alembic/), not by this file.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from . import config

_is_sqlite = config.DATABASE_URL.startswith("sqlite")

# check_same_thread=False is required for SQLite when used with FastAPI's
# threaded request handling; it isn't a valid arg for other drivers, so
# it's SQLite-only. pool_pre_ping guards against managed Postgres
# providers that silently close idle connections — a no-op for SQLite,
# so it's applied whenever we're NOT on SQLite instead of unconditionally.
engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """
    FastAPI dependency that provides a database session per request
    and always closes it afterwards, even if an error occurs.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
