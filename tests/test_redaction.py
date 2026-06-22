# Unit tests for the PDF redaction service (apply_pdf_redactions)
import fitz

from app.schemas import BoundingBox, PdfEntityRead
from app.services.redaction import apply_pdf_redactions


def _entity(bboxes: list[BoundingBox]) -> PdfEntityRead:
    return PdfEntityRead(
        source="COMPREHEND",
        entity_type="NAME",
        text="",
        start_offset=0,
        end_offset=0,
        confidence=0.99,
        bboxes=bboxes,
    )


def _bbox_for(page: fitz.Page, term: str) -> BoundingBox:
    rects = page.search_for(term)
    assert rects, f"'{term}' not found in page after insertion"
    r = rects[0]
    return BoundingBox(
        left=r.x0 / page.rect.width,
        top=r.y0 / page.rect.height,
        width=r.width / page.rect.width,
        height=r.height / page.rect.height,
    )


def test_apply_pdf_redactions_removes_text() -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 144), "REDACT-ME", fontsize=12)
    bbox = _bbox_for(page, "REDACT-ME")
    pdf_bytes = doc.tobytes()
    doc.close()

    redacted = apply_pdf_redactions(pdf_bytes, [_entity([bbox])])

    with fitz.open(stream=redacted, filetype="pdf") as out:
        assert "REDACT-ME" not in out[0].get_text()


def test_apply_pdf_redactions_preserves_other_text() -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 144), "KEEP THIS", fontsize=12)
    page.insert_text((72, 300), "REDACT THIS", fontsize=12)
    bbox = _bbox_for(page, "REDACT THIS")
    pdf_bytes = doc.tobytes()
    doc.close()

    redacted = apply_pdf_redactions(pdf_bytes, [_entity([bbox])])

    with fitz.open(stream=redacted, filetype="pdf") as out:
        text = out[0].get_text()
    assert "REDACT THIS" not in text
    assert "KEEP THIS" in text


def test_apply_pdf_redactions_no_entities_returns_valid_pdf() -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 144), "UNTOUCHED", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()

    result = apply_pdf_redactions(pdf_bytes, [])

    with fitz.open(stream=result, filetype="pdf") as out:
        assert "UNTOUCHED" in out[0].get_text()


def test_apply_pdf_redactions_multiple_bboxes_per_entity() -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 144), "JOHN", fontsize=12)
    page.insert_text((150, 144), "DOE", fontsize=12)
    bbox_john = _bbox_for(page, "JOHN")
    bbox_doe = _bbox_for(page, "DOE")
    pdf_bytes = doc.tobytes()
    doc.close()

    # Single entity with two bboxes — both words must be redacted
    redacted = apply_pdf_redactions(pdf_bytes, [_entity([bbox_john, bbox_doe])])

    with fitz.open(stream=redacted, filetype="pdf") as out:
        text = out[0].get_text()
    assert "JOHN" not in text
    assert "DOE" not in text
