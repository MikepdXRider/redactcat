# Tests for /pdf endpoints — PDF scan and redact
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import fitz
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, UsageEvent
from app.schemas import BoundingBox, DetectedEntity
from app.services.barcodes import BarcodeDetection
from app.services.extraction import WordSpan
from app.services.rekognition import FaceDetection
from app.services.scheduler import JOB_TTL


def _register(client: TestClient, email: str = "user@example.com", password: str = "supersecurepassword") -> dict:
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
def one_page_pdf_with_image() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10))
    pix.clear_with(255)
    rect = fitz.Rect(100, 100, 200, 200)
    page.insert_image(rect, pixmap=pix)
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


def _mock_face() -> list[FaceDetection]:
    return [
        FaceDetection(
            confidence=0.98,
            bbox=BoundingBox(left=0.1, top=0.2, width=0.15, height=0.2),
        )
    ]


def _mock_qr_code() -> list[BarcodeDetection]:
    return [
        BarcodeDetection(
            entity_type="QR_CODE",
            text="https://example.com/user/123",
            bbox=BoundingBox(left=0.1, top=0.1, width=0.2, height=0.2),
        )
    ]


def _mock_barcode() -> list[BarcodeDetection]:
    return [
        BarcodeDetection(
            entity_type="BARCODE",
            text="123456789012",
            bbox=BoundingBox(left=0.1, top=0.1, width=0.3, height=0.05),
        )
    ]


@pytest.fixture(autouse=True)
def mock_schedule_job_expiry():
    with patch("app.routers.pdf.schedule_job_expiry"):
        yield


def _do_scan(client: TestClient, tokens: dict, one_page_pdf: bytes) -> dict:
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("John Doe lives", _mock_word_spans())),
        patch("app.routers.pdf.detect_pii_entities", side_effect=_mock_entities),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
    ):
        return client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()


def _do_redact(client: TestClient, tokens: dict, scan: dict, pdf_bytes: bytes) -> dict:
    with (
        patch("app.routers.pdf.download_from_s3", return_value=pdf_bytes),
        patch("app.routers.pdf.apply_pdf_redactions", return_value=b"redacted"),
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.generate_presigned_url", return_value="https://s3.example.com/redacted.pdf"),
    ):
        return client.post(
            "/pdf/redact",
            json={"job_id": scan["job_id"], "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()


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


def test_scan_file_too_large(client: TestClient) -> None:
    tokens = _register(client)
    # Valid magic bytes but oversized — triggers the size check before PyMuPDF
    oversized = b"%PDF" + b"x" * (10 * 1024 * 1024 + 1)
    response = client.post(
        "/pdf/scan",
        files={"file": ("test.pdf", oversized, "application/pdf")},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 413


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
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"job_id", "entities", "expires_at"}
    assert isinstance(data["job_id"], int)
    assert len(data["entities"]) == 1

    entity = data["entities"][0]
    assert set(entity.keys()) == {"source", "entity_type", "text", "start_offset", "end_offset", "confidence", "bboxes"}
    assert entity["source"] == "COMPREHEND"
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
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
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


def test_scan_aws_failure_does_not_create_job(client_no_raise: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client_no_raise)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", side_effect=Exception("Textract unavailable")),
    ):
        response = client_no_raise.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 500
    assert db.query(Job).count() == 0


def test_scan_creates_job_in_db(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("John Doe", _mock_word_spans())),
        patch("app.routers.pdf.detect_pii_entities", side_effect=_mock_entities),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
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


# --- Rekognition face detection ---

def test_scan_no_images_rekognition_not_called(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("text", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces") as mock_detect_faces,
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    mock_detect_faces.assert_not_called()


def test_scan_with_images_face_detected(client: TestClient, one_page_pdf_with_image: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=_mock_face()),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf_with_image, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data["entities"]) == 1

    entity = data["entities"][0]
    assert entity["source"] == "REKOGNITION"
    assert entity["entity_type"] == "FACE"
    assert entity["text"] == ""
    assert entity["start_offset"] == 0
    assert entity["end_offset"] == 0
    assert pytest.approx(entity["confidence"]) == 0.98
    assert len(entity["bboxes"]) == 1
    assert set(entity["bboxes"][0].keys()) == {"left", "top", "width", "height"}


def test_scan_rekognition_failure_does_not_create_job(
    client_no_raise: TestClient, db: Session, one_page_pdf_with_image: bytes
) -> None:
    tokens = _register(client_no_raise)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", side_effect=Exception("Rekognition unavailable")),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
    ):
        response = client_no_raise.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf_with_image, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 500
    assert db.query(Job).count() == 0


def test_scan_with_images_no_faces(client: TestClient, one_page_pdf_with_image: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf_with_image, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    assert response.json()["entities"] == []


def test_redact_face_entity(client: TestClient, one_page_pdf_with_image: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=_mock_face()),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
    ):
        scan = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf_with_image, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()

    with (
        patch("app.routers.pdf.download_from_s3", return_value=one_page_pdf_with_image),
        patch("app.routers.pdf.apply_pdf_redactions", return_value=b"redacted"),
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.generate_presigned_url", return_value="https://s3.example.com/redacted.pdf"),
    ):
        response = client.post(
            "/pdf/redact",
            json={"job_id": scan["job_id"], "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    assert response.json()["download_url"] == "https://s3.example.com/redacted.pdf"
    assert "expires_at" in response.json()


# --- pyzbar barcode / QR code detection ---

def test_scan_no_barcodes(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("text", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=[]) as mock_detect_barcodes,
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    mock_detect_barcodes.assert_called_once()
    assert response.json()["entities"] == []


def test_scan_qr_code_detected(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=_mock_qr_code()),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data["entities"]) == 1

    entity = data["entities"][0]
    assert entity["source"] == "PYZBAR"
    assert entity["entity_type"] == "QR_CODE"
    assert entity["text"] == "https://example.com/user/123"
    assert entity["confidence"] == 1.0
    assert entity["start_offset"] == 0
    assert entity["end_offset"] == 0
    assert len(entity["bboxes"]) == 1
    assert set(entity["bboxes"][0].keys()) == {"left", "top", "width", "height"}


def test_scan_barcode_detected(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=_mock_barcode()),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data["entities"]) == 1

    entity = data["entities"][0]
    assert entity["source"] == "PYZBAR"
    assert entity["entity_type"] == "BARCODE"
    assert entity["text"] == "123456789012"
    assert entity["confidence"] == 1.0
    assert len(entity["bboxes"]) == 1


def test_redact_pyzbar_entity(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=_mock_qr_code()),
    ):
        scan = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()

    with (
        patch("app.routers.pdf.download_from_s3", return_value=one_page_pdf),
        patch("app.routers.pdf.apply_pdf_redactions", return_value=b"redacted"),
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.generate_presigned_url", return_value="https://s3.example.com/redacted.pdf"),
    ):
        response = client.post(
            "/pdf/redact",
            json={"job_id": scan["job_id"], "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    assert response.json()["download_url"] == "https://s3.example.com/redacted.pdf"
    assert "expires_at" in response.json()


def test_redact_mixed_source_entities(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("John Doe lives", _mock_word_spans())),
        patch("app.routers.pdf.detect_pii_entities", side_effect=_mock_entities),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=_mock_qr_code()),
    ):
        scan = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()

    # Scan response carries entities from two distinct sources
    assert {e["source"] for e in scan["entities"]} == {"COMPREHEND", "PYZBAR"}

    with (
        patch("app.routers.pdf.download_from_s3", return_value=one_page_pdf),
        patch("app.routers.pdf.apply_pdf_redactions", return_value=b"redacted"),
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.generate_presigned_url", return_value="https://s3.example.com/redacted.pdf"),
    ):
        response = client.post(
            "/pdf/redact",
            json={"job_id": scan["job_id"], "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    assert response.json()["download_url"] == "https://s3.example.com/redacted.pdf"
    assert "expires_at" in response.json()


def test_scan_barcode_failure_does_not_create_job(
    client_no_raise: TestClient, db: Session, one_page_pdf: bytes
) -> None:
    tokens = _register(client_no_raise)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", side_effect=Exception("Pyzbar unavailable")),
    ):
        response = client_no_raise.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 500
    assert db.query(Job).count() == 0


# --- POST /pdf/redact ---

def test_redact_unauthenticated(client: TestClient) -> None:
    response = client.post("/pdf/redact", json={"job_id": 1, "entities": []})
    assert response.status_code == 401


def test_redact_invalid_token(client: TestClient) -> None:
    response = client.post(
        "/pdf/redact",
        json={"job_id": 1, "entities": []},
        headers={"Authorization": "Bearer not-valid"},
    )
    assert response.status_code == 401


def test_redact_missing_job(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/pdf/redact",
        json={"job_id": 999999, "entities": []},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 404


def test_redact_wrong_user_job(client: TestClient, one_page_pdf: bytes) -> None:
    tokens_a = _register(client, email="a@example.com")
    scan = _do_scan(client, tokens_a, one_page_pdf)

    tokens_b = _register(client, email="b@example.com")
    response = client.post(
        "/pdf/redact",
        json={"job_id": scan["job_id"], "entities": scan["entities"]},
        headers={"Authorization": f"Bearer {tokens_b['access_token']}"},
    )
    assert response.status_code == 404


def test_redact_returns_download_url(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)

    with (
        patch("app.routers.pdf.download_from_s3", return_value=one_page_pdf),
        patch("app.routers.pdf.apply_pdf_redactions", return_value=b"redacted"),
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.generate_presigned_url", return_value="https://s3.example.com/redacted.pdf"),
    ):
        response = client.post(
            "/pdf/redact",
            json={"job_id": scan["job_id"], "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["download_url"] == "https://s3.example.com/redacted.pdf"
    assert "expires_at" in data


def test_redact_job_row_persists_after_redact(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)
    job_id = scan["job_id"]

    with (
        patch("app.routers.pdf.download_from_s3", return_value=one_page_pdf),
        patch("app.routers.pdf.apply_pdf_redactions", return_value=b"redacted"),
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.generate_presigned_url", return_value="https://s3.example.com/redacted.pdf"),
    ):
        response = client.post(
            "/pdf/redact",
            json={"job_id": job_id, "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    db.expire_all()
    assert db.get(Job, job_id) is not None


def test_redact_expired_job(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)
    job_id = scan["job_id"]

    job = db.get(Job, job_id)
    job.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
    db.commit()

    response = client.post(
        "/pdf/redact",
        json={"job_id": job_id, "entities": []},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert response.status_code == 410
    # Job row is left intact — the Lambda owns cleanup for expired jobs.
    db.expire_all()
    assert db.get(Job, job_id) is not None


def test_redact_s3_not_found_returns_410(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)
    job_id = scan["job_id"]

    no_such_key = ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")

    with patch("app.routers.pdf.download_from_s3", side_effect=no_such_key):
        response = client.post(
            "/pdf/redact",
            json={"job_id": job_id, "entities": []},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 410
    # Job row is left intact — the Lambda owns cleanup.
    db.expire_all()
    assert db.get(Job, job_id) is not None


def test_redact_unexpected_s3_error_returns_500(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)

    unexpected = ClientError({"Error": {"Code": "InternalError", "Message": "oops"}}, "GetObject")

    with patch("app.routers.pdf.download_from_s3", side_effect=unexpected):
        response = client.post(
            "/pdf/redact",
            json={"job_id": scan["job_id"], "entities": []},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 500


# --- Usage event recording ---

def test_scan_records_textract_and_comprehend_events(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    text = "John Doe lives"
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=(text, _mock_word_spans())),
        patch("app.routers.pdf.detect_pii_entities", side_effect=_mock_entities),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    db.expire_all()
    events = {ev.event_type: ev for ev in db.scalars(select(UsageEvent)).all()}

    assert "TEXTRACT_PAGE" in events
    tp = events["TEXTRACT_PAGE"]
    assert tp.input_type == "PDF"
    assert tp.quantity == 1
    assert tp.token_cost == 1500
    assert tp.job_id == job_id

    assert "COMPREHEND_CHAR" in events
    cc = events["COMPREHEND_CHAR"]
    assert cc.input_type == "PDF"
    assert cc.quantity == 300  # 300-char minimum — "John Doe lives" is 14 chars
    assert cc.token_cost == 300
    assert cc.job_id == job_id


def test_scan_records_rekognition_event_when_images_present(
    client: TestClient, db: Session, one_page_pdf_with_image: bytes
) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=_mock_face()),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf_with_image, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    db.expire_all()
    events = db.scalars(select(UsageEvent).where(UsageEvent.event_type == "REKOGNITION_FACE")).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.input_type == "PDF"
    assert ev.quantity == 1
    assert ev.token_cost == 1000
    assert ev.job_id == job_id


def test_scan_no_rekognition_event_without_images(
    client: TestClient, db: Session, one_page_pdf: bytes
) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
    ):
        client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    db.expire_all()
    rekog_events = db.scalars(select(UsageEvent).where(UsageEvent.event_type == "REKOGNITION_FACE")).all()
    assert rekog_events == []


def test_redact_records_pdf_redaction_event(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)
    _do_redact(client, tokens, scan, one_page_pdf)

    db.expire_all()
    events = db.scalars(select(UsageEvent).where(UsageEvent.event_type == "PDF_REDACTION")).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.input_type == "PDF"
    assert ev.quantity == 1
    assert ev.token_cost == 0
    assert ev.job_id == scan["job_id"]


# --- Scheduler and session model ---


def test_scan_calls_schedule_job_expiry(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("John Doe", _mock_word_spans())),
        patch("app.routers.pdf.detect_pii_entities", side_effect=_mock_entities),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
        patch("app.routers.pdf.schedule_job_expiry") as mock_schedule,
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    mock_schedule.assert_called_once()
    s3_key = mock_schedule.call_args.args[0]
    assert s3_key.startswith("pdfs/")
    assert s3_key.endswith("/original.pdf")


def test_scan_scheduler_failure_does_not_fail_scan(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("", [])),
        patch("app.routers.pdf.detect_pii_entities", return_value=[]),
        patch("app.routers.pdf.detect_faces", return_value=[]),
        patch("app.routers.pdf.detect_barcodes", return_value=[]),
        patch("app.routers.pdf.schedule_job_expiry", side_effect=Exception("scheduler unavailable")),
    ):
        response = client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200


def test_scan_response_includes_expires_at(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    before = datetime.now(UTC).replace(tzinfo=None)
    scan = _do_scan(client, tokens, one_page_pdf)
    after = datetime.now(UTC).replace(tzinfo=None)
    expires_at = datetime.fromisoformat(scan["expires_at"])
    assert before + JOB_TTL <= expires_at <= after + JOB_TTL


def test_redact_response_includes_expires_at(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    before = datetime.now(UTC).replace(tzinfo=None)
    scan = _do_scan(client, tokens, one_page_pdf)
    after = datetime.now(UTC).replace(tzinfo=None)
    result = _do_redact(client, tokens, scan, one_page_pdf)
    job_expires_at = datetime.fromisoformat(result["expires_at"])
    assert before + JOB_TTL <= job_expires_at <= after + JOB_TTL
    assert "download_url" in result


def test_redact_unique_key_per_call(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)

    uploaded_keys: list[str] = []

    def capture_upload(data: bytes, bucket: str, key: str) -> None:
        uploaded_keys.append(key)

    with (
        patch("app.routers.pdf.download_from_s3", return_value=one_page_pdf),
        patch("app.routers.pdf.apply_pdf_redactions", return_value=b"redacted"),
        patch("app.routers.pdf.upload_to_s3", side_effect=capture_upload),
        patch("app.routers.pdf.generate_presigned_url", return_value="https://s3.example.com/redacted.pdf"),
    ):
        resp1 = client.post(
            "/pdf/redact",
            json={"job_id": scan["job_id"], "entities": []},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        resp2 = client.post(
            "/pdf/redact",
            json={"job_id": scan["job_id"], "entities": []},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert len(uploaded_keys) == 2
    assert uploaded_keys[0] != uploaded_keys[1]
    assert all("redacted_" in k and k.endswith(".pdf") for k in uploaded_keys)
