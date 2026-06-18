# Health-check router — GET /health returns {"status": "ok"}
from fastapi import APIRouter

from app.schemas import HealthRead

router = APIRouter(tags=["health"])


@router.get("/", response_model=HealthRead)
def health_check() -> HealthRead:
    return HealthRead(status="ok")
