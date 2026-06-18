from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.models import RefreshToken


def test_register_returns_tokens(client: TestClient) -> None:
    response = client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"})
    assert response.status_code == 201
    data = response.json()
    assert set(data.keys()) == {"access_token", "refresh_token", "token_type"}
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["refresh_token"]


def test_register_persists_refresh_token(client: TestClient, db: Session) -> None:
    response = client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"})
    assert response.status_code == 201
    refresh_token = response.json()["refresh_token"]
    row = db.scalar(select(RefreshToken).where(RefreshToken.token == refresh_token))
    assert row is not None
    assert row.user_id is not None
    assert row.expires_at is not None


def test_register_duplicate_email(client: TestClient) -> None:
    payload = {"email": "dupe@example.com", "password": "secret123"}
    client.post("/auth/register", json=payload)
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 409


def test_register_password_too_short(client: TestClient) -> None:
    response = client.post("/auth/register", json={"email": "user@example.com", "password": "short"})
    assert response.status_code == 422


def test_register_invalid_email(client: TestClient) -> None:
    response = client.post("/auth/register", json={"email": "not-an-email", "password": "secret123"})
    assert response.status_code == 422


def test_register_missing_password(client: TestClient) -> None:
    response = client.post("/auth/register", json={"email": "user@example.com"})
    assert response.status_code == 422


def test_register_missing_email(client: TestClient) -> None:
    response = client.post("/auth/register", json={"password": "secret123"})
    assert response.status_code == 422
