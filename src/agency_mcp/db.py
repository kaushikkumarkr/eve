from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str | None = None):
    url = database_url or settings.database_url
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_additive_schema_patches()


def _apply_additive_schema_patches() -> None:
    """Apply small backwards-compatible patches for databases created by earlier MVP builds.

    A full Alembic migration set is appropriate before production rollout. Keeping this
    additive patch here makes the local zero-spend MVP safe to upgrade in place.
    """
    inspector = inspect(engine)
    if "campaign_plans" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("campaign_plans")}
    if "budget" in columns:
        return
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("ALTER TABLE campaign_plans ADD COLUMN IF NOT EXISTS budget JSON"))
        elif engine.dialect.name == "sqlite":
            connection.execute(text("ALTER TABLE campaign_plans ADD COLUMN budget JSON"))


def session_scope():
    return SessionLocal()
