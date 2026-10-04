import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app.api import (
    analytics,
    auth,
    content,
    governance,
    invitations,
    me,
    nba,
    people,
    system,
    users,
)
from app.auth.service import ensure_system_admin
from app.core import http_security
from app.core.config import REPO_ROOT, get_settings
from app.core.db import SessionLocal

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Make sure the fixed administrator account exists before the first request."""
    settings.secret("jwt_secret")  # refuse to start without a signing secret
    try:
        with SessionLocal() as db:
            ensure_system_admin(db)
            db.commit()
    except SQLAlchemyError:
        logging.getLogger("nba").warning("Database not migrated yet; run `alembic upgrade head`.")
    yield


# The interactive API reference is a development aid: it is not served outside a local run.
app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    version="0.1.0",
    description="Explainable, compliance-gated next-best-action engine "
    "for HCP and patient engagement.",
    docs_url="/api/docs" if settings.is_local else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.is_local else None,
)

ROUTERS = (system, auth, invitations, users, nba, people, content, governance, me, analytics)
for module in ROUTERS:
    app.include_router(module.router)

# One deployable unit: when the frontend has been built, this process serves it too.
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

http_security.install(app, FRONTEND_DIST / "index.html")

if (FRONTEND_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        """Client-side routes all resolve to the single-page app."""
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        return FileResponse(FRONTEND_DIST / "index.html")
