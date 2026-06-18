from datetime import datetime
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


# --- login ---

def test_login_returns_tokens(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"})
    response = client.post("/auth/login", json={"email": "user@example.com", "password": "secret123"})
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"access_token", "refresh_token", "token_type"}
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["refresh_token"]


def test_login_wrong_password(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"})
    response = client.post("/auth/login", json={"email": "user@example.com", "password": "wrongpassword"})
    assert response.status_code == 401


def test_login_unknown_email(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": "nobody@example.com", "password": "secret123"})
    assert response.status_code == 401


# --- GET /auth/me ---

def test_me_returns_current_user(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"})
    login = client.post("/auth/login", json={"email": "user@example.com", "password": "secret123"})
    token = login.json()["access_token"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"id", "email", "created_at"}
    assert data["email"] == "user@example.com"


def test_me_invalid_token(client: TestClient) -> None:
    response = client.get("/auth/me", headers={"Authorization": "Bearer not-a-valid-token"})
    assert response.status_code == 401


def test_me_no_token(client: TestClient) -> None:
    response = client.get("/auth/me")
    assert response.status_code == 401


# --- POST /auth/logout ---

def test_logout_deletes_refresh_token(client: TestClient, db: Session) -> None:
    tokens = client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"}).json()
    response = client.post(
        "/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 204
    row = db.scalar(select(RefreshToken).where(RefreshToken.token == tokens["refresh_token"]))
    assert row is None


def test_logout_already_deleted_token_returns_404(client: TestClient) -> None:
    tokens = client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"}).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    body = {"refresh_token": tokens["refresh_token"]}
    client.post("/auth/logout", json=body, headers=headers)
    response = client.post("/auth/logout", json=body, headers=headers)
    assert response.status_code == 404


def test_logout_then_refresh_fails(client: TestClient) -> None:
    tokens = client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"}).json()
    client.post(
        "/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401


def test_logout_other_user_token_returns_404(client: TestClient) -> None:
    tokens_a = client.post("/auth/register", json={"email": "a@example.com", "password": "secret123"}).json()
    tokens_b = client.post("/auth/register", json={"email": "b@example.com", "password": "secret123"}).json()
    response = client.post(
        "/auth/logout",
        json={"refresh_token": tokens_b["refresh_token"]},
        headers={"Authorization": f"Bearer {tokens_a['access_token']}"},
    )
    assert response.status_code == 404


def test_logout_no_auth_returns_401(client: TestClient) -> None:
    tokens = client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"}).json()
    response = client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401


# --- POST /auth/refresh ---

def test_refresh_returns_new_token_pair(client: TestClient, db: Session) -> None:
    tokens = client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"}).json()
    response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"access_token", "refresh_token", "token_type"}
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["refresh_token"] != tokens["refresh_token"]
    old_row = db.scalar(select(RefreshToken).where(RefreshToken.token == tokens["refresh_token"]))
    assert old_row is None


def test_refresh_old_token_rejected_after_rotation(client: TestClient) -> None:
    tokens = client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"}).json()
    client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401


def test_refresh_expired_token_returns_401(client: TestClient, db: Session) -> None:
    tokens = client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"}).json()
    row = db.scalar(select(RefreshToken).where(RefreshToken.token == tokens["refresh_token"]))
    row.expires_at = datetime(2000, 1, 1)
    db.commit()
    response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401


def test_refresh_invalid_token_returns_401(client: TestClient) -> None:
    response = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert response.status_code == 401
