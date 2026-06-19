from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Job, JobEntity


def _register(client: TestClient, email: str = "user@example.com", password: str = "secret123") -> dict:
    return client.post("/auth/register", json={"email": email, "password": password}).json()


def _mock_entities(text: str, job_id: int) -> list[JobEntity]:
    return [
        JobEntity(
            job_id=job_id,
            entity_type="NAME",
            text="John Doe",
            start_offset=0,
            end_offset=8,
            confidence=0.99,
        )
    ]


# --- POST /jobs/text ---

def test_create_text_job_returns_job_with_entities(client: TestClient) -> None:
    tokens = _register(client)
    with patch("app.routers.jobs.detect_pii_entities", side_effect=_mock_entities):
        response = client.post(
            "/jobs/text",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 201
    data = response.json()
    assert set(data.keys()) == {"id", "input_text", "created_at", "entities"}
    assert data["input_text"] == "John Doe lives here"
    assert len(data["entities"]) == 1
    entity = data["entities"][0]
    assert set(entity.keys()) == {"id", "job_id", "entity_type", "text", "start_offset", "end_offset", "confidence"}
    assert entity["entity_type"] == "NAME"
    assert entity["text"] == "John Doe"
    assert entity["start_offset"] == 0
    assert entity["end_offset"] == 8


def test_create_text_job_no_entities(client: TestClient) -> None:
    tokens = _register(client)
    with patch("app.routers.jobs.detect_pii_entities", return_value=[]):
        response = client.post(
            "/jobs/text",
            json={"text": "Nothing sensitive here"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    assert response.status_code == 201
    assert response.json()["entities"] == []


def test_create_text_job_unauthenticated(client: TestClient) -> None:
    response = client.post("/jobs/text", json={"text": "John Doe lives here"})
    assert response.status_code == 401


def test_create_text_job_invalid_token(client: TestClient) -> None:
    response = client.post(
        "/jobs/text",
        json={"text": "John Doe lives here"},
        headers={"Authorization": "Bearer not-valid"},
    )
    assert response.status_code == 401


def test_create_text_job_empty_text(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/jobs/text",
        json={"text": ""},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_create_text_job_text_too_long(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/jobs/text",
        json={"text": "x" * 5001},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


def test_create_text_job_missing_text(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/jobs/text",
        json={},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


# --- GET /jobs/{id}/entities ---

def test_get_job_entities_returns_correct_shape(client: TestClient) -> None:
    tokens = _register(client)
    with patch("app.routers.jobs.detect_pii_entities", side_effect=_mock_entities):
        job_data = client.post(
            "/jobs/text",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()
    response = client.get(
        f"/jobs/{job_data['id']}/entities",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    entities = response.json()
    assert len(entities) == 1
    assert set(entities[0].keys()) == {"id", "job_id", "entity_type", "text", "start_offset", "end_offset", "confidence"}
    assert entities[0]["entity_type"] == "NAME"
    assert entities[0]["text"] == "John Doe"


def test_get_job_entities_cross_user_returns_404(client: TestClient) -> None:
    tokens_a = _register(client, email="a@example.com")
    tokens_b = _register(client, email="b@example.com")
    with patch("app.routers.jobs.detect_pii_entities", side_effect=_mock_entities):
        job_data = client.post(
            "/jobs/text",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens_a['access_token']}"},
        ).json()
    response = client.get(
        f"/jobs/{job_data['id']}/entities",
        headers={"Authorization": f"Bearer {tokens_b['access_token']}"},
    )
    assert response.status_code == 404


def test_get_job_entities_nonexistent_job(client: TestClient) -> None:
    tokens = _register(client)
    response = client.get(
        "/jobs/999/entities",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 404


def test_get_job_entities_unauthenticated(client: TestClient) -> None:
    assert client.get("/jobs/1/entities").status_code == 401


# --- POST /jobs/{id}/redact ---

def test_redact_job_returns_redacted_text(client: TestClient) -> None:
    tokens = _register(client)
    with patch("app.routers.jobs.detect_pii_entities", side_effect=_mock_entities):
        job_data = client.post(
            "/jobs/text",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()
    entity_id = job_data["entities"][0]["id"]
    response = client.post(
        f"/jobs/{job_data['id']}/redact",
        json={"entity_ids": [entity_id]},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"redacted_text"}
    assert data["redacted_text"] == "[REDACTED] lives here"


def test_redact_job_right_to_left_ordering(client: TestClient) -> None:
    tokens = _register(client)

    def _two_entities(text: str, job_id: int) -> list[JobEntity]:
        return [
            JobEntity(job_id=job_id, entity_type="NAME", text="John", start_offset=0, end_offset=4, confidence=0.99),
            JobEntity(job_id=job_id, entity_type="NAME", text="Jane", start_offset=15, end_offset=19, confidence=0.99),
        ]

    with patch("app.routers.jobs.detect_pii_entities", side_effect=_two_entities):
        job_data = client.post(
            "/jobs/text",
            json={"text": "John Smith and Jane"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()

    entity_ids = [e["id"] for e in job_data["entities"]]
    response = client.post(
        f"/jobs/{job_data['id']}/redact",
        json={"entity_ids": entity_ids},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["redacted_text"] == "[REDACTED] Smith and [REDACTED]"


def test_redact_job_empty_entity_ids_returns_original(client: TestClient) -> None:
    tokens = _register(client)
    with patch("app.routers.jobs.detect_pii_entities", side_effect=_mock_entities):
        job_data = client.post(
            "/jobs/text",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()
    response = client.post(
        f"/jobs/{job_data['id']}/redact",
        json={"entity_ids": []},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["redacted_text"] == "John Doe lives here"


def test_redact_job_cross_user_returns_404(client: TestClient) -> None:
    tokens_a = _register(client, email="a@example.com")
    tokens_b = _register(client, email="b@example.com")
    with patch("app.routers.jobs.detect_pii_entities", side_effect=_mock_entities):
        job_data = client.post(
            "/jobs/text",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens_a['access_token']}"},
        ).json()
    response = client.post(
        f"/jobs/{job_data['id']}/redact",
        json={"entity_ids": []},
        headers={"Authorization": f"Bearer {tokens_b['access_token']}"},
    )
    assert response.status_code == 404


def test_redact_job_nonexistent_job(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post(
        "/jobs/999/redact",
        json={"entity_ids": []},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 404


def test_redact_job_unauthenticated(client: TestClient) -> None:
    assert client.post("/jobs/1/redact", json={"entity_ids": []}).status_code == 401


# --- DB-level assertions ---

def test_redact_job_deletes_job_and_entities(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    with patch("app.routers.jobs.detect_pii_entities", side_effect=_mock_entities):
        job_data = client.post(
            "/jobs/text",
            json={"text": "John Doe lives here"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        ).json()
    entity_id = job_data["entities"][0]["id"]
    client.post(
        f"/jobs/{job_data['id']}/redact",
        json={"entity_ids": [entity_id]},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert db.get(Job, job_data["id"]) is None
    assert db.scalars(select(JobEntity).where(JobEntity.job_id == job_data["id"])).all() == []
