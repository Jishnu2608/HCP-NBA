from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.analytics import queries
from app.api.deps import require_roles
from app.core.db import get_db
from app.models import User
from app.models.enums import Role

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/overview")
def overview(
    _: User = Depends(require_roles(Role.ADMIN, Role.COMPLIANCE)), db: Session = Depends(get_db)
) -> dict:
    """Aggregate metrics only: no individual HCP or patient is identifiable here."""
    return queries.overview(db)
