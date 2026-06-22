"""PDF router.

Stateful PII scan and redact for single-page PDFs. The original PDF is stored
ephemerally in S3; entity bounding boxes travel in HTTP payloads rather than the DB.

Flow: POST /pdf/scan uploads the PDF to S3, runs Textract + Comprehend, maps character
offsets to word-level bboxes, and returns them to the client. If the page contains
embedded images, Rekognition detect_faces is also called and FACE entities are appended.
pyzbar is always called to detect QR codes and barcodes (which may be vector graphics
rather than embedded images). The Job row is created only after all calls succeed — if
any raises, there is no orphaned DB row (the S3 object is cleaned up by the bucket
lifecycle rule). POST /pdf/redact atomically claims the Job row via DELETE RETURNING
(filtered by both PK and user_id), commits immediately so concurrent callers see the
deletion, then applies redactions and returns a presigned URL.

Jobs expire after 1 hour. If the job is too old or the original S3 object is gone when
redact is called, we return 410 Gone and clean up any remaining S3 objects.
"""

import secrets
from datetime import UTC, datetime, timedelta

import fitz
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Job, User
from app.schemas import (
    BoundingBox,
    EntitySource,
    EventType,
    InputType,
    PdfEntityRead,
    PdfRedactRead,
    PdfRedactRequest,
    PdfScanRead,
)
from app.services.barcodes import detect_barcodes
from app.services.detection import detect_pii_entities
from app.services.extraction import WordSpan, extract_text_from_pdf_s3
from app.services.redaction import apply_pdf_redactions
from app.services.rekognition import detect_faces
from app.services.storage import delete_from_s3, download_from_s3, generate_presigned_url, upload_to_s3
from app.services.usage import COMPREHEND_MIN_CHARS, record_usage_event

router = APIRouter(tags=["pdf"])

_JOB_TTL = timedelta(hours=1)
_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
_ORIGINAL_FILENAME = "original.pdf"
_REDACTED_FILENAME = "redacted.pdf"


def _bboxes_for_entity(start: int, end: int, word_spans: list[WordSpan]) -> list[BoundingBox]:
    return [
        BoundingBox(left=ws.left, top=ws.top, width=ws.width, height=ws.height)
        for ws in word_spans
        if ws.start_char < end and ws.end_char > start
    ]


def _expire_job(bucket: str, original_key: str) -> None:
    # Best-effort S3 cleanup for expired or missing jobs. DB row is already gone.
    redacted_key = original_key.rsplit("/", 1)[0] + f"/{_REDACTED_FILENAME}"
    for key in [original_key, redacted_key]:
        try:
            delete_from_s3(bucket, key)
        except ClientError:
            pass


@router.post("/scan", response_model=PdfScanRead, status_code=status.HTTP_200_OK)
def scan_pdf(
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PdfScanRead:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="File must be a PDF")

    pdf_bytes = file.file.read()

    if len(pdf_bytes) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds 10 MB limit")

    if pdf_bytes[:4] != b"%PDF":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="File must be a PDF")

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if len(doc) != 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Only single-page PDFs are supported",
            )
        page = doc[0]
        has_images = bool(page.get_images())
        # Always render at 3x (216 DPI) — pyzbar needs sufficient pixel density
        # to decode QR codes and barcodes; 1x (72 DPI) is too coarse and 2x is
        # marginal for small or dense codes. Network latency dominates scan time
        # so the extra render cost is negligible. Also used for Rekognition JPEG.
        # QR codes/barcodes can be vector graphics that don't appear in
        # get_images(), so the render can't be gated on has_images.
        # pix owns its pixel data independently of doc, so it stays valid
        # after this `with` block closes (used by detect_barcodes below).
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
        page_image_bytes = pix.tobytes(output="jpeg") if has_images else None

    s3_key = f"pdfs/{current_user.id}/{secrets.token_urlsafe(16)}/{_ORIGINAL_FILENAME}"
    upload_to_s3(pdf_bytes, settings.S3_BUCKET, s3_key)

    # Job row is created only after all calls succeed. If any raises,
    # the S3 object is orphaned but the lifecycle rule cleans it up — no DB leak.
    text, word_spans = extract_text_from_pdf_s3(settings.S3_BUCKET, s3_key)
    raw_entities = detect_pii_entities(text)
    face_detections = detect_faces(page_image_bytes) if page_image_bytes else []
    barcode_detections = detect_barcodes(pix)

    job = Job(user_id=current_user.id, original_s3_key=s3_key)
    db.add(job)
    db.commit()
    db.refresh(job)

    record_usage_event(db, current_user.id, EventType.TEXTRACT_PAGE, InputType.PDF, quantity=1, job_id=job.id)
    record_usage_event(db, current_user.id, EventType.COMPREHEND_CHAR, InputType.PDF, quantity=max(len(text), COMPREHEND_MIN_CHARS), job_id=job.id)
    if page_image_bytes:
        record_usage_event(db, current_user.id, EventType.REKOGNITION_FACE, InputType.PDF, quantity=1, job_id=job.id)

    text_entities = [
        PdfEntityRead(
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
        PdfEntityRead(
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
        PdfEntityRead(
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

    return PdfScanRead(job_id=job.id, entities=text_entities + face_entities + barcode_entities)


@router.post("/redact", response_model=PdfRedactRead, status_code=status.HTTP_200_OK)
def redact_pdf(
    body: PdfRedactRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PdfRedactRead:
    # Atomically claim the job: DELETE WHERE pk AND user_id so only one concurrent
    # caller wins. Returning raw columns (not the ORM class) avoids SQLAlchemy
    # trying to track the already-deleted instance during commit.
    stmt = (
        delete(Job)
        .where(Job.id == body.job_id, Job.user_id == current_user.id)
        .returning(Job.original_s3_key, Job.created_at)
    )
    row = db.execute(stmt).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    original_s3_key, created_at = row.original_s3_key, row.created_at

    # Commit immediately so concurrent callers see the deletion.
    db.commit()

    if datetime.now(UTC).replace(tzinfo=None) - created_at > _JOB_TTL:
        _expire_job(settings.S3_BUCKET, original_s3_key)
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Job expired")

    try:
        pdf_bytes = download_from_s3(settings.S3_BUCKET, original_s3_key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchKey":
            _expire_job(settings.S3_BUCKET, original_s3_key)
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Job expired") from exc
        raise

    redacted_bytes = apply_pdf_redactions(pdf_bytes, body.entities)

    redacted_key = original_s3_key.rsplit("/", 1)[0] + f"/{_REDACTED_FILENAME}"
    upload_to_s3(redacted_bytes, settings.S3_BUCKET, redacted_key)

    download_url = generate_presigned_url(settings.S3_BUCKET, redacted_key)

    # Delete the original only — the redacted file must remain until the user downloads it.
    # The S3 lifecycle rule (1-day expiration) cleans up the redacted object.
    delete_from_s3(settings.S3_BUCKET, original_s3_key)

    # Job row is already deleted, but the plain-int job_id survives — preserved for client grouping.
    record_usage_event(db, current_user.id, EventType.PDF_REDACTION, InputType.PDF, quantity=1, job_id=body.job_id)

    return PdfRedactRead(download_url=download_url)
