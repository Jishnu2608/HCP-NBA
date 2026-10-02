from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.analytics import queries
from app.api.deps import require_permission
from app.core.db import get_db
from app.core.permissions import Permission
from app.models import User

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/overview")
def overview(
    _: User = Depends(require_permission(Permission.ANALYTICS_READ)), db: Session = Depends(get_db)
) -> dict:
    """Aggregate metrics only: no individual HCP or patient is identifiable here."""
    return queries.overview(db)
