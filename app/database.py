from __future__ import annotations

from collections.abc import Generator

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
        connection.exec_driver_sql("PRAGMA optimize")
