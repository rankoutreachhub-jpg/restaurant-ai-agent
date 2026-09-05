"""
Database connection setup.

We use SQLite for the MVP because it needs zero setup (it's just a file).
Because we go through SQLAlchemy (an ORM), switching to PostgreSQL later
for real multi-restaurant production use is just a one-line change to
DATABASE_URL in config.py — none of the model code or query code has to change.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from . import config

# check_same_thread=False is required for SQLite when used with FastAPI's
# threaded request handling. This is safe for our use case.
engine = create_engine(
    config.DATABASE_URL, connect_args={"check_same_thread": False}
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
