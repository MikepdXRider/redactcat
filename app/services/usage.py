"""Usage event recording service.

Records AWS service call costs as UsageEvent rows for the future billing
enforcement layer. All recording is best-effort: a DB failure logs the error
and rolls back, but never propagates to the caller — the user's request
must complete regardless of whether the event was persisted.

`billing_month_start` is shared by this module, the usage router, and the
enforce_token_limit dependency so all three agree on the same billing period.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import UsageEvent
from app.schemas import EventType, InputType

logger = logging.getLogger(__name__)

# AWS bills a minimum of 300 characters per Comprehend API call.
COMPREHEND_MIN_CHARS = 300

TOKEN_COST_PER_UNIT: dict[EventType, int] = {
    EventType.TEXTRACT_PAGE: 1500,
    EventType.COMPREHEND_CHAR: 1,
    EventType.REKOGNITION_FACE: 1000,
    EventType.PDF_REDACTION: 0,
    EventType.TEXT_REDACTION: 0,
    EventType.IMAGE_REDACTION: 0,
}

MONTHLY_TOKEN_LIMIT = 50_000


def billing_month_start() -> datetime:
    now = datetime.now(UTC).replace(tzinfo=None)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def record_usage_event(
    db: Session,
    user_id: int,
    event_type: EventType,
    input_type: InputType,
    quantity: int,
    job_id: int | None = None,
) -> None:
    # Must be called after the caller's own db.commit() — rollback here only affects this insert.
    try:
        db.add(
            UsageEvent(
                user_id=user_id,
                job_id=job_id,
                event_type=event_type,
                input_type=input_type,
                quantity=quantity,
                token_cost=quantity * TOKEN_COST_PER_UNIT[event_type],
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("usage event insert failed event_type=%s: %s", event_type, type(exc).__name__)
