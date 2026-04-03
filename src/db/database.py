from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from config import settings

from .models import Base

engine = create_engine(settings.db_url, echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate(engine)


def _migrate(eng) -> None:
    """Add columns that may be missing in older databases."""
    insp = inspect(eng)
    if "jobs" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("jobs")}
    if "work_type" not in existing:
        with eng.begin() as conn:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN work_type VARCHAR(64) DEFAULT ''"))


def get_session() -> Session:
    return SessionLocal()
