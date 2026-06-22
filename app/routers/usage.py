"""Usage router.

Reports token consumption for the authenticated user. The billing period resets
on the 1st of each calendar month (midnight UTC). All endpoints are user-scoped
via get_current_user — no URL nesting under /users needed.
"""

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import UsageEvent, User
from app.schemas import UsageEventRead, UsageRead

router = APIRouter(tags=["usage"])


def _billing_month_start() -> datetime:
    now = datetime.now(UTC).replace(tzinfo=None)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@router.get("/summary", response_model=UsageRead, status_code=status.HTTP_200_OK)
def get_usage_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UsageRead:
    cutoff = _billing_month_start()
    # coalesce handles SQL NULL (SUM of zero rows); or 0 satisfies mypy since
    # db.scalar() is typed as int | None regardless of the query expression.
    tokens_used: int = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.token_cost), 0))
        .where(UsageEvent.user_id == current_user.id, UsageEvent.created_at >= cutoff)
    ) or 0
    next_month = cutoff.month % 12 + 1
    next_year = cutoff.year + (1 if cutoff.month == 12 else 0)
    return UsageRead(tokens_used=tokens_used, reset_date=date(next_year, next_month, 1))


@router.get("/history", response_model=list[UsageEventRead], status_code=status.HTTP_200_OK)
def get_usage_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UsageEvent]:
    cutoff = _billing_month_start()
    return list(
        db.scalars(
            select(UsageEvent)
            .where(UsageEvent.user_id == current_user.id, UsageEvent.created_at >= cutoff)
            .order_by(UsageEvent.created_at.desc())
        ).all()
    )
