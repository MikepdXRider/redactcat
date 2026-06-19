# Tests for /pdf endpoints — PDF scan
from unittest.mock import patch

import fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Job
from app.schemas import DetectedEntity
from app.services.extraction import WordSpan


def _register(client: TestClient, email: str = "user@example.com", password: str = "secret123") -> dict:
    return client.post("/auth/register", json={"email": email, "password": password}).json()


@pytest.fixture
def one_page_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((100, 100), "John Doe lives at 123 Main St")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def two_page_pdf() -> bytes:
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _mock_word_spans() -> list[WordSpan]:
    """Word spans matching 'John Doe' assembled from two WORD blocks."""
    return [
        WordSpan(start_char=0, end_char=4, left=0.1, top=0.1, width=0.1, height=0.02),   # John
        WordSpan(start_char=5, end_char=8, left=0.22, top=0.1, width=0.08, height=0.02),  # Doe
        WordSpan(start_char=9, end_char=14, left=0.31, top=0.1, width=0.1, height=0.02),  # lives
    ]


def _mock_entities(_text: str) -> list[DetectedEntity]:
    return [
        DetectedEntity(
            entity_type="NAME",
            text="John Doe",
            start_offset=0,
            end_offset=8,
            confidence=0.99,
        )
    ]


# --- POST /pdf/scan ---

def test_scan_unauthenticated(client: TestClient, one_page_pdf: bytes) -> None:
    response = client.post(
        "/pdf/scan",
        files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
    )
    assert response.status_code == 401


def test_scan_invalid_token(client: TestClient, one_page_pdf: bytes) -> None:
    response = client.post(
        "/pdf/scan",
        files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
        headers={"Authorization": "Bearer not-valid"},
    )
    assert response.status_code == 401


def test_scan_not_a_pdf_content_type(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/pdf/scan",
        files={"file": ("test.txt", b"some text content", "text/plain")},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_scan_invalid_pdf_bytes(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/pdf/scan",
        files={"file": ("test.pdf", b"not a real pdf", "application/pdf")},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_scan_multipage_pdf(client: TestClient, two_page_pdf: bytes) -> None:
    tokens = _register(client)
    response = client.post(
        "/pdf/scan",
        files={"file": ("test.pdf", two_page_pdf, "application/pdf")},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_scan_returns_entities(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("John Doe lives", _mock_word_spans())),
        patch("app.routers.pdf.detect_pii_entities", side_effect=_mock_entities),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"job_id", "entities"}
    assert isinstance(data["job_id"], int)
    assert len(data["entities"]) == 1

    entity = data["entities"][0]
    assert set(entity.keys()) == {"entity_type", "text", "start_offset", "end_offset", "confidence", "bboxes"}
    assert entity["entity_type"] == "NAME"
    assert entity["text"] == "John Doe"
    assert entity["start_offset"] == 0
    assert entity["end_offset"] == 8
    assert len(entity["bboxes"]) == 2  # "John" and "Doe" both overlap [0, 8]

    bbox = entity["bboxes"][0]
    assert set(bbox.keys()) == {"left", "top", "width", "height"}


def test_scan_no_entities(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("Nothing sensitive", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["entities"] == []
    assert isinstance(data["job_id"], int)


def test_scan_creates_job_in_db(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("John Doe", _mock_word_spans())),
        patch("app.routers.pdf.detect_pii_entities", side_effect=_mock_entities),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    job_id = response.json()["job_id"]

    job = db.get(Job, job_id)
    assert job is not None
    assert job.original_s3_key.endswith("/original.pdf")
    assert "pdfs/" in job.original_s3_key
