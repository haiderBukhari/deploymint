"""Postgres engine + session factory. See docs/03-data-model.md §3.3."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from deploymint.config import get_settings
from deploymint.db.models import Base

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_engine(s.database_url, pool_pre_ping=True, echo=s.sql_echo)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())


def get_db():
    """FastAPI dependency."""
    db: Session = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def reset_engine_cache() -> None:
    """Test-only: force get_engine()/get_session_factory() to rebuild against
    whatever DATABASE_URL is currently set."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
