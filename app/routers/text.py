from fastapi import APIRouter, Depends, status

from app.dependencies import get_current_user
from app.models import User
from app.schemas import TextRedactRead, TextRedactRequest, TextScanRead, TextScanRequest
from app.services.detection import detect_pii_entities
from app.services.redaction import apply_text_redactions

router = APIRouter(tags=["text"])


@router.post("/scan", response_model=TextScanRead, status_code=status.HTTP_200_OK)
def scan_text(
    body: TextScanRequest,
    current_user: User = Depends(get_current_user),
) -> TextScanRead:
    return TextScanRead(text=body.text, entities=detect_pii_entities(body.text))


@router.post("/redact", response_model=TextRedactRead, status_code=status.HTTP_200_OK)
def redact_text(
    body: TextRedactRequest,
    current_user: User = Depends(get_current_user),
) -> TextRedactRead:
    return TextRedactRead(redacted_text=apply_text_redactions(body.text, body.entities, body.replacement))
