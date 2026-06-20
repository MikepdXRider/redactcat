"""Text and PDF redaction services.

Text: substitutes entity spans right-to-left so earlier character offsets remain
valid after each replacement. Operates entirely in memory — no I/O.

PDF: opens the file with PyMuPDF, places redact annotations at Textract normalized
bboxes (converted to page points), permanently removes the content, returns bytes.
"""

import fitz
from app.schemas import DetectedEntity, PdfEntityRead


def apply_text_redactions(text: str, entities: list[DetectedEntity], replacement: str = "[REDACTED]") -> str:
    for entity in sorted(entities, key=lambda e: e.start_offset, reverse=True):
        text = text[: entity.start_offset] + replacement + text[entity.end_offset :]
    return text


def apply_pdf_redactions(pdf_bytes: bytes, entities: list[PdfEntityRead]) -> bytes:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        width = page.rect.width
        height = page.rect.height

        for entity in entities:
            for bbox in entity.bboxes:
                rect = fitz.Rect(
                    bbox.left * width,
                    bbox.top * height,
                    (bbox.left + bbox.width) * width,
                    (bbox.top + bbox.height) * height,
                )
                page.add_redact_annot(rect, fill=(0, 0, 0))

        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
        return doc.tobytes(garbage=4, deflate=True, clean=True)
