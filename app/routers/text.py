import boto3
from fastapi import APIRouter, Depends, status

from app.dependencies import get_current_user
from app.models import User
from app.schemas import (
    DetectedEntity,
    TextRedactRead,
    TextRedactRequest,
    TextScanRead,
    TextScanRequest,
)
from app.services.redaction import apply_text_redactions

router = APIRouter(tags=["text"])


def detect_pii_entities(text: str) -> list[DetectedEntity]:
    client = boto3.client("comprehend")
    response = client.detect_pii_entities(Text=text, LanguageCode="en")
    return [
        DetectedEntity(
            entity_type=e["Type"],
            text=text[e["BeginOffset"]: e["EndOffset"]],
            start_offset=e["BeginOffset"],
            end_offset=e["EndOffset"],
            confidence=e["Score"],
        )
        for e in response["Entities"]
    ]


@router.post("/scan", response_model=TextScanRead, status_code=status.HTTP_200_OK)
def scan_text(
    body: TextScanRequest,
    current_user: User = Depends(get_current_user),
) -> TextScanRead:
    return TextScanRead(entities=detect_pii_entities(body.text))


@router.post("/redact", response_model=TextRedactRead, status_code=status.HTTP_200_OK)
def redact_text(
    body: TextRedactRequest,
    current_user: User = Depends(get_current_user),
) -> TextRedactRead:
    return TextRedactRead(redacted_text=apply_text_redactions(body.text, body.entities))
