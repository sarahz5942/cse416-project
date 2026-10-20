"""Database engine, session factory, and session dependency for CampusReserve."""

import os

# CHANGED: nothing was loading .env before this ran. That meant DATABASE_URL
# silently fell back to a hardcoded "postgres:postgres" login (not your real
# one), and authentication.py's `os.environ["JWT_SECRET_KEY"]` -- a hard
# lookup, not .get() -- would crash the whole app on startup with a KeyError,
# since only your test config set that variable.
from dotenv import load_dotenv

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.base import Base  # noqa: F401 — imported so metadata is populated

load_dotenv()


# Connection string from the environment; falls back to a local default for dev.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/campusreserve",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db():
    """FastAPI dependency: yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
