"""PDF router.

Stateful PII scan and redact for single-page PDFs. The original PDF is stored
ephemerally in S3; entity bounding boxes travel in HTTP payloads rather than the DB.

Flow: POST /pdf/scan uploads the PDF to S3, runs Textract + Comprehend, maps character
offsets to word-level bboxes, and returns them to the client. POST /pdf/redact accepts
the (filtered) scan response, applies PyMuPDF redactions, returns a presigned URL, then
deletes both S3 objects and the Job row.
"""

import secrets

import fitz
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Job, User
from app.schemas import BoundingBox, PdfEntityRead, PdfScanRead
from app.services.detection import detect_pii_entities
from app.services.extraction import WordSpan, extract_text_from_pdf_s3
from app.services.storage import upload_to_s3

router = APIRouter(tags=["pdf"])


def _bboxes_for_entity(start: int, end: int, word_spans: list[WordSpan]) -> list[BoundingBox]:
    return [
        BoundingBox(left=ws.left, top=ws.top, width=ws.width, height=ws.height)
        for ws in word_spans
        if ws.start_char < end and ws.end_char > start
    ]


@router.post("/scan", response_model=PdfScanRead, status_code=status.HTTP_200_OK)
def scan_pdf(
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PdfScanRead:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File must be a PDF")

    pdf_bytes = file.file.read()

    if pdf_bytes[:4] != b"%PDF":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File must be a PDF")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    doc.close()
    if page_count != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only single-page PDFs are supported",
        )

    s3_key = f"pdfs/{current_user.id}/{secrets.token_urlsafe(16)}/original.pdf"
    upload_to_s3(pdf_bytes, settings.S3_BUCKET, s3_key)

    job = Job(user_id=current_user.id, original_s3_key=s3_key)
    db.add(job)
    db.commit()
    db.refresh(job)

    text, word_spans = extract_text_from_pdf_s3(settings.S3_BUCKET, s3_key)
    raw_entities = detect_pii_entities(text)

    entities = [
        PdfEntityRead(
            entity_type=e.entity_type,
            text=e.text,
            start_offset=e.start_offset,
            end_offset=e.end_offset,
            confidence=e.confidence,
            bboxes=_bboxes_for_entity(e.start_offset, e.end_offset, word_spans),
        )
        for e in raw_entities
    ]

    return PdfScanRead(job_id=job.id, entities=entities)
