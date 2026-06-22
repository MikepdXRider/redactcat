"""Text router.

Stateless PII scan and redact — no text is written to the DB or S3. /scan calls
Comprehend and returns detected entities; /redact applies substitutions. The scan
response shape matches the redact request body so the client can POST it directly,
controlling which entities to act on. Usage events are recorded as best-effort
side-effects; a recording failure never prevents the response from completing.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import EventType, InputType, TextRedactRead, TextRedactRequest, TextScanRead, TextScanRequest
from app.services.detection import detect_pii_entities
from app.services.redaction import apply_text_redactions
from app.services.usage import record_usage_event

router = APIRouter(tags=["text"])


@router.post("/scan", response_model=TextScanRead, status_code=status.HTTP_200_OK)
def scan_text(
    body: TextScanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TextScanRead:
    entities = detect_pii_entities(body.text)
    record_usage_event(db, current_user.id, EventType.COMPREHEND_CHAR, InputType.TEXT, quantity=max(len(body.text), 300))
    return TextScanRead(text=body.text, entities=entities)


@router.post("/redact", response_model=TextRedactRead, status_code=status.HTTP_200_OK)
def redact_text(
    body: TextRedactRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TextRedactRead:
    redacted_text = apply_text_redactions(body.text, body.entities, body.replacement)
    record_usage_event(db, current_user.id, EventType.TEXT_REDACTION, InputType.TEXT, quantity=1)
    return TextRedactRead(redacted_text=redacted_text)
