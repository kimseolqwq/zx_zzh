from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _enable_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_schema() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        # create_all does not add columns to an existing SQLite table. Keep
        # these small additive migrations explicit so older local databases
        # continue to work without losing collected data.
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(phone_models)")
        }
        if "image_url" not in columns:
            connection.exec_driver_sql("ALTER TABLE phone_models ADD COLUMN image_url VARCHAR(1000)")
        if "image_source_url" not in columns:
            connection.exec_driver_sql("ALTER TABLE phone_models ADD COLUMN image_source_url VARCHAR(500)")
        additive_columns = {
            "wireless_charging_supported": "BOOLEAN",
            "waterproof_supported": "BOOLEAN",
            "nfc": "BOOLEAN",
            "five_g": "BOOLEAN",
            "screen_shape": "VARCHAR(30)",
            "telephoto": "BOOLEAN",
        }
        for name, sql_type in additive_columns.items():
            if name not in columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE phone_models ADD COLUMN {name} {sql_type}"
                )
        recommendation_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(recommendation_runs)")
        }
        run_additive_columns = {
            "intent_model_name": "VARCHAR(120)",
            "intent_latency_ms": "FLOAT",
            "intent_prompt_tokens": "INTEGER",
            "intent_response_tokens": "INTEGER",
            "intent_tokens_per_second": "FLOAT",
            "intent_success": "BOOLEAN",
        }
        for name, sql_type in run_additive_columns.items():
            if name not in recommendation_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE recommendation_runs ADD COLUMN {name} {sql_type}"
                )
        connection.exec_driver_sql("PRAGMA optimize")


def checkpoint_database(database_path: Path | None = None) -> tuple[int, int, int] | None:
    """Flush SQLite WAL pages into the main file for backups and Git snapshots."""
    if database_path is None:
        if engine.dialect.name != "sqlite" or not engine.url.database or engine.url.database == ":memory:":
            return None
        engine.dispose()
        database_path = Path(engine.url.database)
    if not database_path.exists():
        return None
    with sqlite3.connect(database_path) as connection:
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    return tuple(int(value) for value in result) if result else None
