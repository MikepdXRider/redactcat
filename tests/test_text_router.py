from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApiKey, User, UsageEvent
from app.schemas import DetectedEntity, EventType, InputType


def _register(client: TestClient, email: str = "user@example.com", password: str = "supersecurepassword") -> dict:
    return client.post("/auth/register", json={"email": email, "password": password}).json()


def _mock_entities(text: str) -> list[DetectedEntity]:
    return [
        DetectedEntity(
            entity_type="NAME",
            text="John Doe",
            start_offset=0,
            end_offset=8,
            confidence=0.99,
        )
    ]


def _seed_usage(db: Session, user_id: int, token_cost: int, created_at: datetime | None = None) -> None:
    db.add(UsageEvent(
        user_id=user_id,
        event_type=EventType.COMPREHEND_CHAR,
        input_type=InputType.TEXT,
        quantity=token_cost,
        token_cost=token_cost,
        created_at=created_at if created_at is not None else datetime.now(UTC).replace(tzinfo=None),
    ))
    db.commit()


# --- POST /text/scan ---

def test_scan_unauthenticated(client: TestClient) -> None:
    assert client.post("/text/scan", json={"text": "John Doe lives here"}).status_code == 401


def test_scan_invalid_token(client: TestClient) -> None:
    response = client.post(
        "/text/scan",
        json={"text": "John Doe lives here"},
        headers={"Authorization": "Bearer not-valid"},
    )
    assert response.status_code == 401


def test_scan_returns_entities(client: TestClient) -> None:
    tokens = _register(client)
    with patch("app.routers.text.detect_pii_entities", side_effect=_mock_entities):
        response = client.post(
            "/text/scan",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"text", "entities"}
    assert data["text"] == "John Doe lives here"
    assert len(data["entities"]) == 1
    entity = data["entities"][0]
    assert set(entity.keys()) == {"entity_type", "text", "start_offset", "end_offset", "confidence"}
    assert entity["entity_type"] == "NAME"
    assert entity["text"] == "John Doe"
    assert entity["start_offset"] == 0
    assert entity["end_offset"] == 8


def test_scan_no_entities_detected(client: TestClient) -> None:
    tokens = _register(client)
    with patch("app.routers.text.detect_pii_entities", return_value=[]):
        response = client.post(
            "/text/scan",
            json={"text": "Nothing sensitive here"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 200
    assert response.json() == {"text": "Nothing sensitive here", "entities": []}


def test_scan_empty_text(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/scan",
        json={"text": ""},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_scan_text_too_long(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/scan",
        json={"text": "x" * 5001},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_scan_missing_text(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/scan",
        json={},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_scan_records_comprehend_usage_event(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    text = "John Doe lives here"
    with patch("app.routers.text.detect_pii_entities", return_value=[]):
        response = client.post(
            "/text/scan",
            json={"text": text},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 200

    db.expire_all()
    events = db.scalars(select(UsageEvent).where(UsageEvent.event_type == "COMPREHEND_CHAR")).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.input_type == "TEXT"
    assert ev.quantity == 300  # 300-char minimum — "John Doe lives here" is 19 chars
    assert ev.token_cost == 300
    assert ev.job_id is None


def test_scan_records_comprehend_min_300_chars(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    with patch("app.routers.text.detect_pii_entities", return_value=[]):
        client.post(
            "/text/scan",
            json={"text": "Hi"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    db.expire_all()
    ev = db.scalars(select(UsageEvent).where(UsageEvent.event_type == "COMPREHEND_CHAR")).one()
    assert ev.quantity == 300
    assert ev.token_cost == 300


def test_scan_records_comprehend_actual_quantity_above_minimum(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    long_text = "x" * 500
    with patch("app.routers.text.detect_pii_entities", return_value=[]):
        client.post(
            "/text/scan",
            json={"text": long_text},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    db.expire_all()
    ev = db.scalars(select(UsageEvent).where(UsageEvent.event_type == "COMPREHEND_CHAR")).one()
    assert ev.quantity == 500
    assert ev.token_cost == 500


# --- POST /text/redact ---

def _entity(entity_type: str, text: str, start: int, end: int, confidence: float = 0.99) -> dict:
    return {
        "entity_type": entity_type,
        "text": text,
        "start_offset": start,
        "end_offset": end,
        "confidence": confidence,
    }


def test_redact_unauthenticated(client: TestClient) -> None:
    assert client.post(
        "/text/redact",
        json={"text": "John Doe lives here", "entities": []},
    ).status_code == 401


def test_redact_invalid_token(client: TestClient) -> None:
    response = client.post(
        "/text/redact",
        json={"text": "John Doe lives here", "entities": []},
        headers={"Authorization": "Bearer not-valid"},
    )
    assert response.status_code == 401


def test_redact_returns_redacted_text(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/redact",
        json={
            "text": "John Doe lives here",
            "entities": [_entity("NAME", "John Doe", 0, 8)],
        },
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"redacted_text"}
    assert data["redacted_text"] == "[REDACTED] lives here"


def test_redact_empty_entities_returns_original(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/redact",
        json={"text": "John Doe lives here", "entities": []},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["redacted_text"] == "John Doe lives here"


def test_redact_right_to_left_ordering(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/redact",
        json={
            "text": "John Smith and Jane",
            "entities": [
                _entity("NAME", "John", 0, 4),
                _entity("NAME", "Jane", 15, 19),
            ],
        },
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["redacted_text"] == "[REDACTED] Smith and [REDACTED]"


def test_redact_empty_text(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/redact",
        json={"text": "", "entities": []},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_redact_text_too_long(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/redact",
        json={"text": "x" * 5001, "entities": []},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_redact_custom_replacement(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/redact",
        json={
            "text": "John Doe lives here",
            "entities": [_entity("NAME", "John Doe", 0, 8)],
            "replacement": "******",
        },
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["redacted_text"] == "****** lives here"


def test_redact_empty_replacement_deletes_pii(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/redact",
        json={
            "text": "John Doe lives here",
            "entities": [_entity("NAME", "John Doe", 0, 8)],
            "replacement": "",
        },
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["redacted_text"] == " lives here"


def test_redact_missing_fields(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/redact",
        json={"text": "John Doe lives here"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_redact_records_text_redaction_event(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/redact",
        json={"text": "John Doe lives here", "entities": []},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200

    db.expire_all()
    events = db.scalars(select(UsageEvent).where(UsageEvent.event_type == "TEXT_REDACTION")).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.input_type == "TEXT"
    assert ev.quantity == 1
    assert ev.token_cost == 0
    assert ev.job_id is None


# --- API key auth ---


def _api_key(client: TestClient, tokens: dict) -> str:
    return client.post("/users/me/api-key", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()["key"]


def test_scan_with_api_key(client: TestClient) -> None:
    tokens = _register(client)
    key = _api_key(client, tokens)
    with patch("app.routers.text.detect_pii_entities", return_value=[]):
        response = client.post(
            "/text/scan",
            json={"text": "Hello world here"},
            headers={"Authorization": f"Bearer {key}"},
        )
    assert response.status_code == 200
    assert "entities" in response.json()


def test_redact_with_api_key(client: TestClient) -> None:
    tokens = _register(client)
    key = _api_key(client, tokens)
    response = client.post(
        "/text/redact",
        json={"text": "John Doe lives here", "entities": [_entity("NAME", "John Doe", 0, 8)]},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 200
    assert response.json()["redacted_text"] == "[REDACTED] lives here"


def test_scan_invalid_api_key_returns_401(client: TestClient) -> None:
    response = client.post(
        "/text/scan",
        json={"text": "Hello world here"},
        headers={"Authorization": "Bearer rcat_notarealkey"},
    )
    assert response.status_code == 401


def test_scan_api_key_updates_last_used_at(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    key = _api_key(client, tokens)
    with patch("app.routers.text.detect_pii_entities", return_value=[]):
        client.post(
            "/text/scan",
            json={"text": "Hello world here"},
            headers={"Authorization": f"Bearer {key}"},
        )
    db.expire_all()
    row = db.scalar(select(ApiKey))
    assert row is not None
    assert row.last_used_at is not None


def test_scan_api_key_resolves_to_correct_user(client: TestClient, db: Session) -> None:
    tokens_a = _register(client, "a@example.com")
    _register(client, "b@example.com")
    key_a = _api_key(client, tokens_a)
    with patch("app.routers.text.detect_pii_entities", return_value=[]):
        client.post(
            "/text/scan",
            json={"text": "Hello world here"},
            headers={"Authorization": f"Bearer {key_a}"},
        )
    db.expire_all()
    user_a = db.scalar(select(User).where(User.email == "a@example.com"))
    ev = db.scalar(select(UsageEvent).where(UsageEvent.event_type == "COMPREHEND_CHAR"))
    assert ev is not None
    assert ev.user_id == user_a.id


# --- Token limit enforcement ---


def test_scan_429_when_at_token_limit(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    user_id = db.scalar(select(User).where(User.email == "user@example.com")).id
    _seed_usage(db, user_id, 50_000)
    response = client.post(
        "/text/scan",
        json={"text": "John Doe lives here"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert set(detail.keys()) == {"error", "tokens_used", "tokens_allowed", "resets_in_days"}
    assert detail["error"] == "token_limit_reached"
    assert detail["tokens_used"] == 50_000
    assert detail["tokens_allowed"] == 50_000
    assert 1 <= detail["resets_in_days"] <= 31


def test_scan_allowed_when_under_token_limit(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    user_id = db.scalar(select(User).where(User.email == "user@example.com")).id
    _seed_usage(db, user_id, 49_999)
    with patch("app.routers.text.detect_pii_entities", return_value=[]):
        response = client.post(
            "/text/scan",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 200


def test_scan_ignores_usage_from_last_month(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    user_id = db.scalar(select(User).where(User.email == "user@example.com")).id
    _seed_usage(db, user_id, 50_000, created_at=datetime(2020, 1, 1))
    with patch("app.routers.text.detect_pii_entities", return_value=[]):
        response = client.post(
            "/text/scan",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 200


def test_scan_token_limit_isolated_per_user(client: TestClient, db: Session) -> None:
    tokens_a = _register(client, "a@example.com")
    _register(client, "b@example.com")
    user_b_id = db.scalar(select(User).where(User.email == "b@example.com")).id
    _seed_usage(db, user_b_id, 50_000)
    with patch("app.routers.text.detect_pii_entities", return_value=[]):
        response = client.post(
            "/text/scan",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens_a['access_token']}"},
        )
    assert response.status_code == 200


def test_redact_not_gated_by_token_limit(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    user_id = db.scalar(select(User).where(User.email == "user@example.com")).id
    _seed_usage(db, user_id, 50_000)
    response = client.post(
        "/text/redact",
        json={"text": "John Doe lives here", "entities": []},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
