"""Image router.

Stateful PII scan and redact for JPEG and PNG images. Mirrors the PDF flow:
the original image is stored ephemerally in S3; entity bounding boxes travel
in HTTP payloads rather than the DB.

Flow: POST /image/scan uploads the image to S3, schedules a one-time EventBridge
cleanup Lambda ~1 hour out, runs Textract (OCR) + Comprehend (PII detection), maps
character offsets to word-level bboxes, calls Rekognition detect_faces, and runs
pyzbar barcode/QR detection. Returns all detected entities with normalized bboxes
and an expires_at timestamp.

POST /image/redact applies Pillow black-box redactions at the supplied bboxes,
uploads a unique output key, and returns a presigned download URL. Does not delete
the Job row — multiple redact calls on the same job produce distinct output files
that coexist until Lambda cleanup fires ~1 hour after scan.
"""

import io
import logging
import secrets
from datetime import UTC, datetime

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import enforce_token_limit, get_current_user_any_auth
from app.models import Job, User
from app.schemas import (
    BoundingBox,
    EntitySource,
    ErrorRead,
    EventType,
    ImageEntityRead,
    ImageRedactRead,
    ImageRedactRequest,
    ImageScanRead,
    InputType,
    TokenLimitErrorRead,
)
from app.services.barcodes import detect_barcodes
from app.services.detection import detect_pii_entities
from app.services.extraction import WordSpan, extract_text_from_s3_object
from app.services.image_redaction import apply_image_redactions
from app.services.rekognition import detect_faces
from app.services.scheduler import JOB_TTL, schedule_job_expiry
from app.services.storage import download_from_s3, generate_presigned_url, upload_to_s3
from app.services.usage import COMPREHEND_MIN_CHARS, record_usage_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["image"])

_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB — Textract synchronous + Rekognition inline limit

_CONTENT_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
_MAGIC_BYTES = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG",
}


def _bboxes_for_entity(start: int, end: int, word_spans: list[WordSpan]) -> list[BoundingBox]:
    return [
        BoundingBox(left=ws.left, top=ws.top, width=ws.width, height=ws.height)
        for ws in word_spans
        if ws.start_char < end and ws.end_char > start
    ]


@router.post(
    "/scan",
    response_model=ImageScanRead,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorRead, "description": "Invalid or expired token."},
        413: {"model": ErrorRead, "description": "File exceeds 5 MB."},
        422: {"model": ErrorRead, "description": "File is not a valid JPEG or PNG."},
        429: {"model": TokenLimitErrorRead, "description": "Monthly token budget exhausted."},
    },
)
def scan_image(
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(enforce_token_limit),
) -> ImageScanRead:
    """Upload a JPEG or PNG image and scan it for PII.

    **Consumes tokens:**
    - Textract: 1,500 per image (always)
    - Comprehend: 1 per character, minimum 300
    - Rekognition: 1,000 per image (always)

    The image is stored in S3 for approximately one hour (the job TTL). Use the
    returned `job_id` with `POST /image/redact` to apply redactions within that window.
    QR code and barcode detection always runs (no token cost).

    **Constraints:** JPEG or PNG, max 5 MB.
    """
    if file.content_type not in _CONTENT_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="File must be a JPEG or PNG image")

    image_bytes = file.file.read()

    if len(image_bytes) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds 5 MB limit")

    content_type: str = file.content_type  # type: ignore[assignment]
    magic = _MAGIC_BYTES[content_type]
    if image_bytes[: len(magic)] != magic:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="File must be a JPEG or PNG image")

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="File must be a JPEG or PNG image")

    ext = _CONTENT_TYPES[content_type]
    s3_key = f"images/{current_user.id}/{secrets.token_urlsafe(16)}/original.{ext}"
    upload_to_s3(image_bytes, settings.S3_BUCKET, s3_key)

    try:
        schedule_job_expiry(s3_key)
    except Exception:
        logger.warning("failed to schedule job expiry for token=%s", s3_key.split("/")[2])

    text, word_spans = extract_text_from_s3_object(settings.S3_BUCKET, s3_key)
    raw_entities = detect_pii_entities(text)
    face_detections = detect_faces(image_bytes)
    # Re-open after verify() — verify() exhausts the image object.
    barcode_detections = detect_barcodes(Image.open(io.BytesIO(image_bytes)))

    job = Job(user_id=current_user.id, original_s3_key=s3_key)
    db.add(job)
    db.commit()
    db.refresh(job)

    record_usage_event(db, current_user.id, EventType.TEXTRACT_PAGE, InputType.IMAGE, quantity=1, job_id=job.id)
    record_usage_event(db, current_user.id, EventType.COMPREHEND_CHAR, InputType.IMAGE, quantity=max(len(text), COMPREHEND_MIN_CHARS), job_id=job.id)
    record_usage_event(db, current_user.id, EventType.REKOGNITION_FACE, InputType.IMAGE, quantity=1, job_id=job.id)

    text_entities = [
        ImageEntityRead(
            source=EntitySource.COMPREHEND,
            entity_type=e.entity_type,
            text=e.text,
            start_offset=e.start_offset,
            end_offset=e.end_offset,
            confidence=e.confidence,
            bboxes=_bboxes_for_entity(e.start_offset, e.end_offset, word_spans),
        )
        for e in raw_entities
    ]
    face_entities = [
        ImageEntityRead(
            source=EntitySource.REKOGNITION,
            entity_type="FACE",
            text="",
            start_offset=0,
            end_offset=0,
            confidence=face.confidence,
            bboxes=[face.bbox],
        )
        for face in face_detections
    ]
    barcode_entities = [
        ImageEntityRead(
            source=EntitySource.PYZBAR,
            entity_type=b.entity_type,
            text=b.text,
            start_offset=0,
            end_offset=0,
            confidence=1.0,
            bboxes=[b.bbox],
        )
        for b in barcode_detections
    ]

    return ImageScanRead(
        job_id=job.id,
        entities=text_entities + face_entities + barcode_entities,
        expires_at=job.created_at + JOB_TTL,
    )


@router.post(
    "/redact",
    response_model=ImageRedactRead,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorRead, "description": "Invalid or expired token."},
        404: {"model": ErrorRead, "description": "Job not found or belongs to another user."},
        410: {"model": ErrorRead, "description": "Job has expired and the original image has been deleted."},
    },
)
def redact_image(
    body: ImageRedactRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_auth),
) -> ImageRedactRead:
    """Apply redactions to a previously scanned image.

    Each call produces a unique redacted output file with its own pre-signed download
    URL. Multiple redact calls on the same job are allowed. Makes no AWS inference
    calls and consumes no tokens.

    Returns 404 if the job belongs to another user. Returns 410 if the job TTL has
    elapsed or the original image has already been deleted.
    """
    job = db.get(Job, body.job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if datetime.now(UTC).replace(tzinfo=None) - job.created_at > JOB_TTL:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Job expired")

    try:
        image_bytes = download_from_s3(settings.S3_BUCKET, job.original_s3_key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Job expired") from exc
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Storage error") from exc

    redacted_bytes = apply_image_redactions(image_bytes, body.entities)

    # Derive extension from the original key so the output file has the same format.
    original_ext = job.original_s3_key.rsplit(".", 1)[-1]
    redacted_key = job.original_s3_key.rsplit("/", 1)[0] + f"/redacted_{secrets.token_urlsafe(8)}.{original_ext}"
    upload_to_s3(redacted_bytes, settings.S3_BUCKET, redacted_key)
    download_url = generate_presigned_url(settings.S3_BUCKET, redacted_key)

    record_usage_event(db, current_user.id, EventType.IMAGE_REDACTION, InputType.IMAGE, quantity=1, job_id=body.job_id)

    return ImageRedactRead(download_url=download_url, expires_at=job.created_at + JOB_TTL)
