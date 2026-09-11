"""
GET /health — liveness check.
"""
from fastapi import APIRouter
from app.core.config import settings

router = APIRouter()


@router.get("/health", summary="Health check")
async def health_check():
    """Returns 200 when the API process is alive."""
    return {"status": "ok", "project": settings.PROJECT_NAME}
