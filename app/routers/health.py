"""Health-check router.

Returns {"status": "ok"} — used by App Runner to verify the container is
accepting traffic after deployment.
"""

from fastapi import APIRouter

from app.schemas import HealthRead

router = APIRouter(tags=["health"])


@router.get("/", response_model=HealthRead)
def health_check() -> HealthRead:
    return HealthRead(status="ok")
