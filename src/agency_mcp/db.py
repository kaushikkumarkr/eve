from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
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


def _alembic_config() -> Config:
    package_dir = Path(__file__).resolve().parent
    repository_root = next(
        (
            root
            for root in (package_dir.parent.parent, package_dir.parent)
            if (root / "alembic.ini").is_file() and (root / "migrations" / "env.py").is_file()
        ),
        None,
    )
    if repository_root is None:
        raise RuntimeError("Eve Alembic configuration and migrations are missing from this installation")
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    config.set_main_option("script_location", str(repository_root / "migrations"))
    return config


def migrate_db() -> None:
    """Apply Alembic migrations. Invoke only as an explicit admin/deployment action."""
    from . import models  # noqa: F401

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    config = _alembic_config()
    if not tables:
        command.upgrade(config, "head")
        return
    if "alembic_version" not in tables:
        # Explicitly adopt a local legacy rehearsal database without deleting its
        # records. New deployments always take the versioned migration path.
        Base.metadata.create_all(bind=engine)
        _apply_additive_schema_patches()
        command.stamp(config, "head")
        return
    command.upgrade(config, "head")


_BOOTSTRAP_EVENTS = frozenset({
    "container_started", "db_connect_started", "db_connected", "migration_succeeded", "db_role_succeeded",
    "keyvault_auth_succeeded", "keyvault_write_succeeded", "failed_db_connect",
    "failed_migration", "failed_db_role", "failed_keyvault_auth", "failed_keyvault_write",
    "db_role_lookup_succeeded", "db_role_validation_succeeded", "db_role_ddl_succeeded",
    "db_role_connect_grant_succeeded", "db_role_schema_grant_succeeded",
    "db_role_table_grant_succeeded", "db_role_sequence_grant_succeeded",
    "db_role_default_table_grant_succeeded", "db_role_default_sequence_grant_succeeded",
    "failed_db_role_lookup", "failed_db_role_validation", "failed_db_role_ddl",
    "failed_db_role_connect_grant", "failed_db_role_schema_grant", "failed_db_role_table_grant",
    "failed_db_role_sequence_grant", "failed_db_role_default_table_grant",
    "failed_db_role_default_sequence_grant",
    "failed_db_role_ddl_execute_create", "failed_db_role_ddl_execute_alter",
})

BOOTSTRAP_FAILURE_EXIT_CODES = {
    "db_connect": 20,
    "migration": 21,
    "db_role": 22,
    "keyvault_auth": 23,
    "keyvault_write": 24,
    "db_role_lookup": 30,
    "db_role_validation": 31,
    "db_role_ddl": 32,
    "db_role_connect_grant": 33,
    "db_role_schema_grant": 34,
    "db_role_table_grant": 35,
    "db_role_sequence_grant": 36,
    "db_role_default_table_grant": 37,
    "db_role_default_sequence_grant": 38,
    "db_role_ddl_execute_create": 41,
    "db_role_ddl_execute_alter": 42,
}


class BootstrapFailure(RuntimeError):
    """A bootstrap error exposing only a fixed stage identifier."""

    def __init__(self, stage: str) -> None:
        if stage not in BOOTSTRAP_FAILURE_EXIT_CODES:
            raise ValueError("Unsupported bootstrap failure stage")
        self.stage = stage
        super().__init__(f"Eve bootstrap failed at stage {stage}")


def bootstrap_runtime_database(
    *, secret_name: str, vault_url: str, emit_event: Callable[[str], None] | None = None
) -> None:
    """Migrate a new database and place a generated least-privilege app URL in Key Vault.

    This is intended only for a one-shot deployment job running with a temporary
    Key Vault secret-write identity and the PostgreSQL bootstrap connection.
    The bootstrap login and generated application password are never returned.
    """
    import secrets

    from sqlalchemy import text

    if engine.dialect.name != "postgresql":
        raise RuntimeError("Database bootstrap requires PostgreSQL")
    if not secret_name or not vault_url.startswith("https://"):
        raise RuntimeError("A Key Vault URL and database secret name are required")

    def emit(code: str) -> None:
        if code not in _BOOTSTRAP_EVENTS:
            raise ValueError("Unsupported bootstrap event")
        if emit_event is not None:
            emit_event(code)

    try:
        emit("db_connect_started")
        with engine.connect():
            pass
        emit("db_connected")
    except Exception:
        emit("failed_db_connect")
        raise BootstrapFailure("db_connect") from None

    try:
        migrate_db()
        emit("migration_succeeded")
    except Exception:
        emit("failed_migration")
        raise BootstrapFailure("migration") from None

    app_role = "eve_app"
    app_password = secrets.token_urlsafe(36)
    admin_role = engine.url.username
    if not admin_role:
        raise RuntimeError("The bootstrap connection must identify its PostgreSQL role")

    stage = "db_role_lookup"
    try:
        with engine.begin() as connection:
            existing_role = connection.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                    "FROM pg_roles WHERE rolname = :role"
                ),
                {"role": app_role},
            ).one_or_none()
            emit("db_role_lookup_succeeded")
            stage = "db_role_validation"
            # Normalize the dedicated Eve role instead of refusing to recover it.
            # The CREATE/ALTER statement below explicitly clears every elevated flag.
            emit("db_role_validation_succeeded")
            ddl_action = "alter" if existing_role else "create"
            stage = f"db_role_ddl_execute_{ddl_action}"
            # PostgreSQL's format() placeholders (%I/%L) conflict with psycopg's
            # parameter syntax. The identifier is fixed and the generated password
            # is restricted to URL/SQL-safe ASCII characters before interpolation.
            if not app_password.isascii() or not all(
                char.isalnum() or char in "-_" for char in app_password
            ):
                raise RuntimeError("Generated database password failed validation")
            quoted_role = engine.dialect.identifier_preparer.quote(app_role)
            role_ddl = (
                f"{('ALTER' if existing_role else 'CREATE')} ROLE {quoted_role} "
                f"WITH LOGIN PASSWORD '{app_password}' NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            connection.exec_driver_sql(role_ddl)
            stage = "db_role_connect_grant"
            connection.exec_driver_sql(f"GRANT CONNECT ON DATABASE {engine.dialect.identifier_preparer.quote(engine.url.database)} TO eve_app")
            emit("db_role_connect_grant_succeeded")
            stage = "db_role_schema_grant"
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO eve_app")
            emit("db_role_schema_grant_succeeded")
            stage = "db_role_table_grant"
            connection.exec_driver_sql("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eve_app")
            emit("db_role_table_grant_succeeded")
            stage = "db_role_sequence_grant"
            connection.exec_driver_sql("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO eve_app")
            emit("db_role_sequence_grant_succeeded")
            owner = engine.dialect.identifier_preparer.quote(admin_role)
            stage = "db_role_default_table_grant"
            connection.exec_driver_sql(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eve_app"
            )
            emit("db_role_default_table_grant_succeeded")
            stage = "db_role_default_sequence_grant"
            connection.exec_driver_sql(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
                "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO eve_app"
            )
            emit("db_role_default_sequence_grant_succeeded")
        emit("db_role_succeeded")
    except BootstrapFailure:
        raise
    except Exception:
        emit(f"failed_{stage}")
        raise BootstrapFailure(stage) from None

    runtime_url = engine.url.set(username=app_role, password=app_password).render_as_string(
        hide_password=False
    )
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    try:
        credential = DefaultAzureCredential()
        credential.get_token("https://vault.azure.net/.default")
        emit("keyvault_auth_succeeded")
    except Exception:
        emit("failed_keyvault_auth")
        raise BootstrapFailure("keyvault_auth") from None

    client = SecretClient(vault_url=vault_url, credential=credential)
    try:
        client.set_secret(secret_name, runtime_url)
        emit("keyvault_write_succeeded")
    except Exception:
        emit("failed_keyvault_write")
        raise BootstrapFailure("keyvault_write") from None
    finally:
        client.close()
        credential.close()


def verify_db_schema() -> None:
    """Read-only startup gate. Services never perform schema changes themselves."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "alembic_version" not in tables:
        raise RuntimeError("Eve database is not migrated; an admin must run `agency db-upgrade` first")
    with engine.connect() as connection:
        current = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one_or_none()
    config = _alembic_config()
    head = ScriptDirectory.from_config(config).get_current_head()
    if current != head:
        raise RuntimeError(
            f"Eve database schema is not at the application revision ({current!r} != {head!r}); "
            "an admin must run `agency db-upgrade` before starting services"
        )


def _apply_additive_schema_patches() -> None:
    """Apply small backwards-compatible patches for databases created by earlier MVP builds.

    Versioned Alembic migrations are required for new deployments. This path only
    adopts a legacy local rehearsal database without destructive reconstruction.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    additions = {
        "clients": {"profile": "JSON"},
        "source_documents": {
            "content_hash": "VARCHAR(64)",
            "redaction_summary": "JSON",
            "source_url": "VARCHAR(2048)",
            "retrieved_at": "VARCHAR(100)",
            "source_metadata": "JSON",
        },
        "evidence": {"source_url": "VARCHAR(2048)", "source_locator": "VARCHAR(300)", "evidence_type": "VARCHAR(50)", "review_status": "VARCHAR(30)"},
        "audit_logs": {"client_id": "VARCHAR(36)", "actor_id": "VARCHAR(200)", "previous_hash": "VARCHAR(64)", "entry_hash": "VARCHAR(64)"},
        "ads_insight_snapshots": {"aggregation_level": "VARCHAR(30)", "timezone": "VARCHAR(100)", "requested_fields": "JSON", "raw_response": "JSON", "freshness_state": "VARCHAR(50)", "api_version": "VARCHAR(30)"},
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
    from sqlalchemy import text

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
