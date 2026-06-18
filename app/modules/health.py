# Health-check router — GET /health returns {"status": "ok"}
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
