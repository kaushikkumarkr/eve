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
    tables = set(inspector.get_table_names())
    additions = {
        "campaign_plans": {"budget": "JSON"},
        "clients": {"profile": "JSON"},
        "source_documents": {
            "content_hash": "VARCHAR(64)",
            "redaction_summary": "JSON",
        },
    }
    pending: list[tuple[str, str, str]] = []
    for table, columns_to_add in additions.items():
        if table not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        pending.extend(
            (table, column, sql_type)
            for column, sql_type in columns_to_add.items()
            if column not in existing
        )
    if not pending:
        return
    with engine.begin() as connection:
        for table, column, sql_type in pending:
            if engine.dialect.name == "postgresql":
                connection.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type}")
                )
            elif engine.dialect.name == "sqlite":
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def session_scope():
    return SessionLocal()
