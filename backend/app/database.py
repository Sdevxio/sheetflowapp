import sqlite3
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()
_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=not _sqlite,
    connect_args={"check_same_thread": False, "timeout": 30} if _sqlite else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    autocommit = dbapi_connection.autocommit
    dbapi_connection.autocommit = True
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    dbapi_connection.autocommit = autocommit


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
