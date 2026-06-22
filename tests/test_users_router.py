# Tests for /users endpoints — profile read, update, and delete
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RefreshToken


def _register(client: TestClient, email: str = "user@example.com", password: str = "secret123") -> dict:
    return client.post("/auth/register", json={"email": email, "password": password}).json()


def _auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# --- GET /users/me ---

def test_get_me_returns_current_user(client: TestClient) -> None:
    tokens = _register(client)
    response = client.get("/users/me", headers=_auth(tokens))
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"id", "email", "created_at"}
    assert data["email"] == "user@example.com"


def test_get_me_invalid_token(client: TestClient) -> None:
    response = client.get("/users/me", headers={"Authorization": "Bearer not-a-valid-token"})
    assert response.status_code == 401


def test_get_me_no_token(client: TestClient) -> None:
    response = client.get("/users/me")
    assert response.status_code == 401


# --- PATCH /users/me ---

def test_update_email(client: TestClient) -> None:
    tokens = _register(client)
    response = client.patch("/users/me", json={"email": "new@example.com"}, headers=_auth(tokens))
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"id", "email", "created_at"}
    assert data["email"] == "new@example.com"


def test_update_email_to_taken_email(client: TestClient) -> None:
    _register(client, email="a@example.com")
    tokens_b = _register(client, email="b@example.com")
    response = client.patch("/users/me", json={"email": "a@example.com"}, headers=_auth(tokens_b))
    assert response.status_code == 409


def test_update_password_correct_current(client: TestClient) -> None:
    tokens = _register(client)
    response = client.patch(
        "/users/me",
        json={"current_password": "secret123", "new_password": "newpassword1"},
        headers=_auth(tokens),
    )
    assert response.status_code == 200
    login = client.post("/auth/login", json={"email": "user@example.com", "password": "newpassword1"})
    assert login.status_code == 200


def test_update_password_wrong_current(client: TestClient) -> None:
    tokens = _register(client)
    response = client.patch(
        "/users/me",
        json={"current_password": "wrongpassword", "new_password": "newpassword1"},
        headers=_auth(tokens),
    )
    assert response.status_code == 401


def test_update_new_password_without_current_password(client: TestClient) -> None:
    tokens = _register(client)
    response = client.patch("/users/me", json={"new_password": "newpassword1"}, headers=_auth(tokens))
    assert response.status_code == 401


def test_update_new_password_too_short(client: TestClient) -> None:
    tokens = _register(client)
    response = client.patch(
        "/users/me",
        json={"current_password": "secret123", "new_password": "short"},
        headers=_auth(tokens),
    )
    assert response.status_code == 422


def test_update_unauthenticated(client: TestClient) -> None:
    response = client.patch("/users/me", json={"email": "new@example.com"})
    assert response.status_code == 401


# --- DELETE /users/me ---

def test_delete_me(client: TestClient) -> None:
    tokens = _register(client)
    response = client.delete("/users/me", headers=_auth(tokens))
    assert response.status_code == 204


def test_delete_me_purges_refresh_tokens(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    client.delete("/users/me", headers=_auth(tokens))
    rows = db.scalars(select(RefreshToken)).all()
    assert rows == []


def test_delete_me_access_token_rejected_after(client: TestClient) -> None:
    tokens = _register(client)
    client.delete("/users/me", headers=_auth(tokens))
    response = client.get("/users/me", headers=_auth(tokens))
    assert response.status_code == 401


def test_delete_me_login_fails_after(client: TestClient) -> None:
    _register(client)
    tokens = client.post("/auth/login", json={"email": "user@example.com", "password": "secret123"}).json()
    client.delete("/users/me", headers=_auth(tokens))
    response = client.post("/auth/login", json={"email": "user@example.com", "password": "secret123"})
    assert response.status_code == 401


def test_delete_me_unauthenticated(client: TestClient) -> None:
    response = client.delete("/users/me")
    assert response.status_code == 401
