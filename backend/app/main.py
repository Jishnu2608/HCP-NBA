from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import analytics, auth, content, governance, me, nba, people, system
from app.core.config import REPO_ROOT, get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Explainable, compliance-gated next-best-action engine "
    "for HCP and patient engagement.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

for module in (system, auth, nba, people, content, governance, me, analytics):
    app.include_router(module.router)

# One deployable unit: when the frontend has been built, this process serves it too.
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

if (FRONTEND_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        """Client-side routes all resolve to the single-page app."""
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        return FileResponse(FRONTEND_DIST / "index.html")
