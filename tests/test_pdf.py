# Tests for /pdf endpoints — PDF scan and redact
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import fitz
import pytest
from botocore.exceptions import ClientError
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


def _do_scan(client: TestClient, tokens: dict, one_page_pdf: bytes) -> dict:
    with (
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.extract_text_from_pdf_s3", return_value=("John Doe lives", _mock_word_spans())),
        patch("app.routers.pdf.detect_pii_entities", side_effect=_mock_entities),
    ):
        return client.post(
            "/pdf/scan",
            files={"file": ("test.pdf", one_page_pdf, "application/pdf")},
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
        patch("app.routers.pdf.delete_from_s3"),
    ):
        response = client.post(
            "/pdf/redact",
            json={"job_id": scan["job_id"], "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    assert response.json() == {"download_url": "https://s3.example.com/redacted.pdf"}


def test_redact_deletes_job_from_db(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)
    job_id = scan["job_id"]

    with (
        patch("app.routers.pdf.download_from_s3", return_value=one_page_pdf),
        patch("app.routers.pdf.apply_pdf_redactions", return_value=b"redacted"),
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.generate_presigned_url", return_value="https://s3.example.com/redacted.pdf"),
        patch("app.routers.pdf.delete_from_s3"),
    ):
        client.post(
            "/pdf/redact",
            json={"job_id": job_id, "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    db.expire_all()
    assert db.get(Job, job_id) is None


def test_redact_calls_s3_cleanup(client: TestClient, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)

    with (
        patch("app.routers.pdf.download_from_s3", return_value=one_page_pdf),
        patch("app.routers.pdf.apply_pdf_redactions", return_value=b"redacted"),
        patch("app.routers.pdf.upload_to_s3"),
        patch("app.routers.pdf.generate_presigned_url", return_value="https://s3.example.com/redacted.pdf"),
        patch("app.routers.pdf.delete_from_s3") as mock_delete,
    ):
        client.post(
            "/pdf/redact",
            json={"job_id": scan["job_id"], "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert mock_delete.call_count == 1
    deleted_key = mock_delete.call_args.args[1]
    assert deleted_key.endswith("/original.pdf")


def test_redact_expired_job(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)
    job_id = scan["job_id"]

    job = db.get(Job, job_id)
    job.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
    db.commit()

    with patch("app.routers.pdf.delete_from_s3"):
        response = client.post(
            "/pdf/redact",
            json={"job_id": job_id, "entities": []},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 410
    db.expire_all()
    assert db.get(Job, job_id) is None


def test_redact_s3_not_found_returns_410(client: TestClient, db: Session, one_page_pdf: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, one_page_pdf)
    job_id = scan["job_id"]

    no_such_key = ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")

    with (
        patch("app.routers.pdf.download_from_s3", side_effect=no_such_key),
        patch("app.routers.pdf.delete_from_s3"),
    ):
        response = client.post(
            "/pdf/redact",
            json={"job_id": job_id, "entities": []},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 410
    db.expire_all()
    assert db.get(Job, job_id) is None
