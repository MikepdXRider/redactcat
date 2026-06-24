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
from app.dependencies import enforce_token_limit, get_current_user_any_auth
from app.models import User
from app.schemas import (
    ErrorRead,
    EventType,
    InputType,
    TextRedactRead,
    TextRedactRequest,
    TextScanRead,
    TextScanRequest,
    TokenLimitErrorRead,
)
from app.services.detection import detect_pii_entities
from app.services.redaction import apply_text_redactions
from app.services.usage import COMPREHEND_MIN_CHARS, record_usage_event

router = APIRouter(tags=["text"])


@router.post(
    "/scan",
    response_model=TextScanRead,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorRead, "description": "Invalid or expired token."},
        429: {"model": TokenLimitErrorRead, "description": "Monthly token budget exhausted."},
    },
)
def scan_text(
    body: TextScanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(enforce_token_limit),
) -> TextScanRead:
    """Scan plain text for PII using AWS Comprehend.

    Returns the original text and a list of detected entities with character offsets
    and confidence scores. **Consumes tokens** (1 per character, minimum 300 billed).

    Pass the full response — or a filtered subset of `entities` — directly to
    `POST /text/redact`. Returns 429 when the monthly token budget is exhausted.
    """
    entities = detect_pii_entities(body.text)
    record_usage_event(db, current_user.id, EventType.COMPREHEND_CHAR, InputType.TEXT, quantity=max(len(body.text), COMPREHEND_MIN_CHARS))
    return TextScanRead(text=body.text, entities=entities)


@router.post(
    "/redact",
    response_model=TextRedactRead,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorRead, "description": "Invalid or expired token."},
    },
)
def redact_text(
    body: TextRedactRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_auth),
) -> TextRedactRead:
    """Apply redactions to plain text.

    Replaces each entity's character span with the `replacement` string. Makes no
    AWS calls and consumes no tokens. The `entities` list may be the full scan
    response or a filtered subset — only the provided entities are redacted.
    """
    redacted_text = apply_text_redactions(body.text, body.entities, body.replacement)
    record_usage_event(db, current_user.id, EventType.TEXT_REDACTION, InputType.TEXT, quantity=1)
    return TextRedactRead(redacted_text=redacted_text)
