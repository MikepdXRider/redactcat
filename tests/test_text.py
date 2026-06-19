from unittest.mock import patch

from fastapi.testclient import TestClient

from app.schemas import DetectedEntity


def _register(client: TestClient, email: str = "user@example.com", password: str = "secret123") -> dict:
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
    assert set(data.keys()) == {"entities"}
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
    assert response.json() == {"entities": []}


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


# --- POST /text/redact ---

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
            "entities": [{"start_offset": 0, "end_offset": 8}],
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
                {"start_offset": 0, "end_offset": 4},
                {"start_offset": 15, "end_offset": 19},
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


def test_redact_missing_fields(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/text/redact",
        json={"text": "John Doe lives here"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422
