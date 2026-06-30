import io
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, UsageEvent
from app.schemas import BoundingBox, DetectedEntity, EventType
from app.services.barcodes import BarcodeDetection
from app.services.extraction import WordSpan
from app.services.rekognition import FaceDetection
from app.services.scheduler import JOB_TTL

from conftest import _seed_usage


def _register(client: TestClient, email: str = "user@example.com", password: str = "supersecurepassword") -> dict:
    return client.post("/auth/register", json={"email": email, "password": password}).json()


def _make_image_bytes(fmt: str = "JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(255, 255, 255)).save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def jpeg_bytes() -> bytes:
    return _make_image_bytes("JPEG")


@pytest.fixture
def png_bytes() -> bytes:
    return _make_image_bytes("PNG")


def _mock_word_spans() -> list[WordSpan]:
    return [
        WordSpan(start_char=0, end_char=4, left=0.1, top=0.1, width=0.1, height=0.02),
        WordSpan(start_char=5, end_char=8, left=0.22, top=0.1, width=0.08, height=0.02),
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


def _mock_barcode() -> list[BarcodeDetection]:
    return [
        BarcodeDetection(
            entity_type="QR_CODE",
            text="https://example.com",
            bbox=BoundingBox(left=0.1, top=0.1, width=0.2, height=0.2),
        )
    ]


@pytest.fixture(autouse=True)
def mock_schedule_job_expiry():
    with patch("app.routers.image.schedule_job_expiry"):
        yield


def _do_scan(client: TestClient, tokens: dict, image: bytes, content_type: str = "image/jpeg") -> dict:
    with (
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.extract_text_from_s3_object", return_value=("John Doe", _mock_word_spans())),
        patch("app.routers.image.detect_pii_entities", side_effect=_mock_entities),
        patch("app.routers.image.detect_faces", return_value=[]),
        patch("app.routers.image.detect_barcodes", return_value=[]),
    ):
        return client.post(
            "/image/scan",
            files={"file": ("test.jpg", image, content_type)},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()


def _do_redact(client: TestClient, tokens: dict, scan: dict) -> dict:
    with (
        patch("app.routers.image.download_from_s3", return_value=b"\xff\xd8\xff\xe0" + b"\x00" * 20 + b"\xff\xd9"),
        patch("app.routers.image.apply_image_redactions", return_value=b"redacted"),
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.generate_presigned_url", return_value="https://s3.example.com/redacted.jpg"),
    ):
        return client.post(
            "/image/redact",
            json={"job_id": scan["job_id"], "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()


# --- POST /image/scan ---

def test_scan_unauthenticated(client: TestClient, jpeg_bytes: bytes) -> None:
    response = client.post(
        "/image/scan",
        files={"file": ("test.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert response.status_code == 401


def test_scan_invalid_token(client: TestClient, jpeg_bytes: bytes) -> None:
    response = client.post(
        "/image/scan",
        files={"file": ("test.jpg", jpeg_bytes, "image/jpeg")},
        headers={"Authorization": "Bearer not-valid"},
    )
    assert response.status_code == 401


def test_scan_wrong_content_type(client: TestClient, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    response = client.post(
        "/image/scan",
        files={"file": ("test.pdf", jpeg_bytes, "application/pdf")},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_scan_bad_magic_bytes_jpeg(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/image/scan",
        files={"file": ("test.jpg", b"not a real image", "image/jpeg")},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_scan_bad_magic_bytes_png(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/image/scan",
        files={"file": ("test.png", b"not a real image", "image/png")},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_scan_corrupt_body_with_valid_magic_bytes(client: TestClient) -> None:
    tokens = _register(client)
    corrupt = b"\xff\xd8\xff" + b"\x00" * 100  # valid JPEG magic, corrupt body
    response = client.post(
        "/image/scan",
        files={"file": ("test.jpg", corrupt, "image/jpeg")},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_scan_file_too_large(client: TestClient) -> None:
    tokens = _register(client)
    oversized = b"\xff\xd8\xff" + b"x" * (5 * 1024 * 1024 + 1)
    response = client.post(
        "/image/scan",
        files={"file": ("test.jpg", oversized, "image/jpeg")},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 413


def test_scan_token_limit_exceeded(client: TestClient, db: Session, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    user = db.execute(select(__import__("app.models", fromlist=["User"]).User)).scalars().first()
    assert user is not None
    _seed_usage(db, user.id, 50_000)
    response = client.post(
        "/image/scan",
        files={"file": ("test.jpg", jpeg_bytes, "image/jpeg")},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 429


def test_scan_returns_entity_shape(client: TestClient, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.extract_text_from_s3_object", return_value=("John Doe", _mock_word_spans())),
        patch("app.routers.image.detect_pii_entities", side_effect=_mock_entities),
        patch("app.routers.image.detect_faces", return_value=[]),
        patch("app.routers.image.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/image/scan",
            files={"file": ("test.jpg", jpeg_bytes, "image/jpeg")},
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
    assert set(entity["bboxes"][0].keys()) == {"left", "top", "width", "height"}


def test_scan_face_entity_shape(client: TestClient, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.extract_text_from_s3_object", return_value=("", [])),
        patch("app.routers.image.detect_pii_entities", return_value=[]),
        patch("app.routers.image.detect_faces", return_value=_mock_face()),
        patch("app.routers.image.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/image/scan",
            files={"file": ("test.jpg", jpeg_bytes, "image/jpeg")},
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


def test_scan_barcode_entity_shape(client: TestClient, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.extract_text_from_s3_object", return_value=("", [])),
        patch("app.routers.image.detect_pii_entities", return_value=[]),
        patch("app.routers.image.detect_faces", return_value=[]),
        patch("app.routers.image.detect_barcodes", return_value=_mock_barcode()),
    ):
        response = client.post(
            "/image/scan",
            files={"file": ("test.jpg", jpeg_bytes, "image/jpeg")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data["entities"]) == 1
    entity = data["entities"][0]
    assert entity["source"] == "PYZBAR"
    assert entity["entity_type"] == "QR_CODE"
    assert entity["text"] == "https://example.com"
    assert entity["confidence"] == 1.0
    assert len(entity["bboxes"]) == 1


def test_scan_png_accepted(client: TestClient, png_bytes: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.extract_text_from_s3_object", return_value=("", [])),
        patch("app.routers.image.detect_pii_entities", return_value=[]),
        patch("app.routers.image.detect_faces", return_value=[]),
        patch("app.routers.image.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/image/scan",
            files={"file": ("test.png", png_bytes, "image/png")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 200


def test_scan_job_row_created(client: TestClient, db: Session, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.extract_text_from_s3_object", return_value=("John Doe", _mock_word_spans())),
        patch("app.routers.image.detect_pii_entities", side_effect=_mock_entities),
        patch("app.routers.image.detect_faces", return_value=[]),
        patch("app.routers.image.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/image/scan",
            files={"file": ("test.jpg", jpeg_bytes, "image/jpeg")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    job_id = response.json()["job_id"]

    job = db.get(Job, job_id)
    assert job is not None
    assert job.original_s3_key.endswith("/original.jpg")
    assert "images/" in job.original_s3_key


def test_scan_png_job_key_has_png_extension(client: TestClient, db: Session, png_bytes: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.extract_text_from_s3_object", return_value=("", [])),
        patch("app.routers.image.detect_pii_entities", return_value=[]),
        patch("app.routers.image.detect_faces", return_value=[]),
        patch("app.routers.image.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/image/scan",
            files={"file": ("test.png", png_bytes, "image/png")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    job = db.get(Job, response.json()["job_id"])
    assert job is not None
    assert job.original_s3_key.endswith("/original.png")


def test_scan_usage_events_recorded(client: TestClient, db: Session, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    with (
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.extract_text_from_s3_object", return_value=("John Doe", _mock_word_spans())),
        patch("app.routers.image.detect_pii_entities", side_effect=_mock_entities),
        patch("app.routers.image.detect_faces", return_value=[]),
        patch("app.routers.image.detect_barcodes", return_value=[]),
    ):
        response = client.post(
            "/image/scan",
            files={"file": ("test.jpg", jpeg_bytes, "image/jpeg")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 200
    job_id = response.json()["job_id"]

    events = db.execute(select(UsageEvent).where(UsageEvent.job_id == job_id)).scalars().all()
    event_types = {e.event_type for e in events}
    assert EventType.TEXTRACT_PAGE in event_types
    assert EventType.COMPREHEND_CHAR in event_types
    assert EventType.REKOGNITION_FACE in event_types


def test_scan_aws_failure_does_not_create_job(client_no_raise: TestClient, db: Session, jpeg_bytes: bytes) -> None:
    tokens = _register(client_no_raise)
    with (
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.extract_text_from_s3_object", side_effect=Exception("Textract unavailable")),
    ):
        response = client_no_raise.post(
            "/image/scan",
            files={"file": ("test.jpg", jpeg_bytes, "image/jpeg")},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 500
    assert db.query(Job).count() == 0


# --- POST /image/redact ---

def test_redact_unauthenticated(client: TestClient, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, jpeg_bytes)
    response = client.post(
        "/image/redact",
        json={"job_id": scan["job_id"], "entities": scan["entities"]},
    )
    assert response.status_code == 401


def test_redact_job_not_found(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/image/redact",
        json={"job_id": 99999, "entities": []},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 404


def test_redact_wrong_user(client: TestClient, jpeg_bytes: bytes) -> None:
    owner_tokens = _register(client, email="owner@example.com")
    other_tokens = _register(client, email="other@example.com")
    scan = _do_scan(client, owner_tokens, jpeg_bytes)

    response = client.post(
        "/image/redact",
        json={"job_id": scan["job_id"], "entities": scan["entities"]},
        headers={"Authorization": f"Bearer {other_tokens['access_token']}"},
    )
    assert response.status_code == 404


def test_redact_expired_job(client: TestClient, db: Session, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, jpeg_bytes)

    job = db.get(Job, scan["job_id"])
    assert job is not None
    job.created_at = datetime.now(UTC).replace(tzinfo=None) - JOB_TTL - timedelta(seconds=1)
    db.commit()

    response = client.post(
        "/image/redact",
        json={"job_id": scan["job_id"], "entities": scan["entities"]},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 410


def test_redact_missing_s3_object(client: TestClient, jpeg_bytes: bytes) -> None:
    from botocore.exceptions import ClientError

    tokens = _register(client)
    scan = _do_scan(client, tokens, jpeg_bytes)

    no_such_key = ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject")
    with patch("app.routers.image.download_from_s3", side_effect=no_such_key):
        response = client.post(
            "/image/redact",
            json={"job_id": scan["job_id"], "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 410


def test_redact_returns_download_url(client: TestClient, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, jpeg_bytes)
    redact = _do_redact(client, tokens, scan)

    assert set(redact.keys()) == {"download_url", "expires_at"}
    assert redact["download_url"] == "https://s3.example.com/redacted.jpg"
    assert redact["expires_at"] is not None


def test_redact_unique_s3_key_per_call(client: TestClient, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, jpeg_bytes)

    uploaded_keys: list[str] = []

    def capture_upload(data: bytes, bucket: str, key: str) -> None:
        uploaded_keys.append(key)

    with (
        patch("app.routers.image.download_from_s3", return_value=jpeg_bytes),
        patch("app.routers.image.apply_image_redactions", return_value=b"redacted"),
        patch("app.routers.image.upload_to_s3", side_effect=capture_upload),
        patch("app.routers.image.generate_presigned_url", return_value="https://s3.example.com/redacted.jpg"),
    ):
        client.post(
            "/image/redact",
            json={"job_id": scan["job_id"], "entities": []},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        client.post(
            "/image/redact",
            json={"job_id": scan["job_id"], "entities": []},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert len(uploaded_keys) == 2
    assert uploaded_keys[0] != uploaded_keys[1]
    assert all(k.endswith(".jpg") for k in uploaded_keys)


def test_redact_via_api_key(client: TestClient, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    api_key_resp = client.post(
        "/users/me/api-key",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    ).json()
    raw_key = api_key_resp["key"]

    scan = _do_scan(client, tokens, jpeg_bytes)

    with (
        patch("app.routers.image.download_from_s3", return_value=jpeg_bytes),
        patch("app.routers.image.apply_image_redactions", return_value=b"redacted"),
        patch("app.routers.image.upload_to_s3"),
        patch("app.routers.image.generate_presigned_url", return_value="https://s3.example.com/redacted.jpg"),
    ):
        response = client.post(
            "/image/redact",
            json={"job_id": scan["job_id"], "entities": scan["entities"]},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    assert response.status_code == 200


def test_redact_job_row_persists_after_redact(client: TestClient, db: Session, jpeg_bytes: bytes) -> None:
    tokens = _register(client)
    scan = _do_scan(client, tokens, jpeg_bytes)
    _do_redact(client, tokens, scan)

    assert db.get(Job, scan["job_id"]) is not None
