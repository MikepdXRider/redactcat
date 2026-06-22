"""Usage event recording service.

Records AWS service call costs as UsageEvent rows for the future billing
enforcement layer. All recording is best-effort: a DB failure logs the error
and rolls back, but never propagates to the caller — the user's request
must complete regardless of whether the event was persisted.
"""

import logging

from sqlalchemy.orm import Session

from app.models import UsageEvent
from app.schemas import EventType, InputType

logger = logging.getLogger(__name__)

TOKEN_COST_PER_UNIT: dict[EventType, int] = {
    EventType.TEXTRACT_PAGE: 1500,
    EventType.COMPREHEND_CHAR: 1,
    EventType.REKOGNITION_FACE: 1000,
    EventType.PDF_REDACTION: 0,
    EventType.TEXT_REDACTION: 0,
}


def record_usage_event(
    db: Session,
    user_id: int,
    event_type: EventType,
    input_type: InputType,
    quantity: int,
    job_id: int | None = None,
) -> None:
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
