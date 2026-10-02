from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import get_today
from app.core.config import get_settings
from app.core.db import Base, get_db

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/meta")
def meta(db: Session = Depends(get_db)) -> dict:
    """Environment, demo clock and row counts per table."""
    settings = get_settings()
    counts = {
        table.name: db.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }
    return {
        "app": settings.app_name,
        "environment": settings.environment,
        "database": settings.database_url.split(":", 1)[0],
        "llm_provider": settings.llm_provider,
        "as_of_date": get_today(db).isoformat(),
        "row_counts": counts,
    }
