# Tests for /users endpoints — profile read, update, delete, and API key management
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApiKey, RefreshToken
from app.services.auth import API_KEY_PREFIX, hash_api_key

PASSWORD = "supersecurepassword"
NEW_PASSWORD = "newsecurepassword1"


def _register(client: TestClient, email: str = "user@example.com", password: str = PASSWORD) -> dict:
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
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=_auth(tokens),
    )
    assert response.status_code == 200
    login = client.post("/auth/login", json={"email": "user@example.com", "password": NEW_PASSWORD})
    assert login.status_code == 200


def test_update_password_wrong_current(client: TestClient) -> None:
    tokens = _register(client)
    response = client.patch(
        "/users/me",
        json={"current_password": "wrongpassword", "new_password": NEW_PASSWORD},
        headers=_auth(tokens),
    )
    assert response.status_code == 401


def test_update_new_password_without_current_password(client: TestClient) -> None:
    tokens = _register(client)
    response = client.patch("/users/me", json={"new_password": NEW_PASSWORD}, headers=_auth(tokens))
    assert response.status_code == 401


def test_update_new_password_too_short(client: TestClient) -> None:
    tokens = _register(client)
    response = client.patch(
        "/users/me",
        json={"current_password": PASSWORD, "new_password": "short"},
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
    tokens = client.post("/auth/login", json={"email": "user@example.com", "password": PASSWORD}).json()
    client.delete("/users/me", headers=_auth(tokens))
    response = client.post("/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    assert response.status_code == 401


def test_delete_me_unauthenticated(client: TestClient) -> None:
    response = client.delete("/users/me")
    assert response.status_code == 401


# --- POST /users/me/api-key ---

def test_generate_api_key_returns_key(client: TestClient) -> None:
    tokens = _register(client)
    response = client.post("/users/me/api-key", headers=_auth(tokens))
    assert response.status_code == 201
    data = response.json()
    assert set(data.keys()) == {"key"}
    assert data["key"].startswith(API_KEY_PREFIX)


def test_generate_api_key_stores_hash_not_raw(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    key = client.post("/users/me/api-key", headers=_auth(tokens)).json()["key"]
    row = db.scalar(select(ApiKey))
    assert row is not None
    assert row.key_hash == hash_api_key(key)
    assert row.key_hash != key


def test_generate_api_key_stores_prefix(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    key = client.post("/users/me/api-key", headers=_auth(tokens)).json()["key"]
    row = db.scalar(select(ApiKey))
    assert row is not None
    assert row.key_prefix == key[:len(API_KEY_PREFIX) + 8]


def test_rotate_api_key_replaces_old_key(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    first_key = client.post("/users/me/api-key", headers=_auth(tokens)).json()["key"]
    second_key = client.post("/users/me/api-key", headers=_auth(tokens)).json()["key"]
    assert first_key != second_key
    rows = db.scalars(select(ApiKey)).all()
    assert len(rows) == 1
    assert rows[0].key_hash == hash_api_key(second_key)


def test_generate_api_key_requires_auth(client: TestClient) -> None:
    assert client.post("/users/me/api-key").status_code == 401


# --- GET /users/me/api-key ---

def test_get_api_key_metadata_returns_prefix_and_timestamps(client: TestClient) -> None:
    tokens = _register(client)
    key = client.post("/users/me/api-key", headers=_auth(tokens)).json()["key"]
    response = client.get("/users/me/api-key", headers=_auth(tokens))
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"key_prefix", "created_at", "last_used_at"}
    assert data["key_prefix"] == key[:len(API_KEY_PREFIX) + 8]
    assert data["last_used_at"] is None


def test_get_api_key_metadata_404_when_no_key(client: TestClient) -> None:
    tokens = _register(client)
    assert client.get("/users/me/api-key", headers=_auth(tokens)).status_code == 404


def test_get_api_key_metadata_requires_auth(client: TestClient) -> None:
    assert client.get("/users/me/api-key").status_code == 401


# --- DELETE /users/me/api-key ---

def test_revoke_api_key_deletes_row(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    client.post("/users/me/api-key", headers=_auth(tokens))
    response = client.delete("/users/me/api-key", headers=_auth(tokens))
    assert response.status_code == 204
    assert db.scalar(select(ApiKey)) is None


def test_revoke_api_key_no_op_when_none_exists(client: TestClient) -> None:
    tokens = _register(client)
    assert client.delete("/users/me/api-key", headers=_auth(tokens)).status_code == 204


def test_revoke_api_key_second_delete_is_no_op(client: TestClient) -> None:
    tokens = _register(client)
    client.post("/users/me/api-key", headers=_auth(tokens))
    client.delete("/users/me/api-key", headers=_auth(tokens))
    assert client.delete("/users/me/api-key", headers=_auth(tokens)).status_code == 204


def test_revoke_api_key_requires_auth(client: TestClient) -> None:
    assert client.delete("/users/me/api-key").status_code == 401


# --- Cross-user isolation ---

def test_api_key_isolated_per_user(client: TestClient) -> None:
    tokens_a = _register(client, "a@example.com")
    tokens_b = _register(client, "b@example.com")
    client.post("/users/me/api-key", headers=_auth(tokens_a))
    assert client.get("/users/me/api-key", headers=_auth(tokens_b)).status_code == 404


# --- API key cannot manage itself ---

def test_api_key_cannot_call_jwt_only_endpoints(client: TestClient) -> None:
    tokens = _register(client)
    key = client.post("/users/me/api-key", headers=_auth(tokens)).json()["key"]
    # /users/me/api-key uses get_current_user (JWT-only); an API key must be rejected
    assert client.post("/users/me/api-key", headers={"Authorization": f"Bearer {key}"}).status_code == 401


# NOTE: get_current_user_accept_api_key is not yet tested here because no endpoint
# currently uses it. Tests for valid API key auth, last_used_at updates, invalid key
# rejection, and cross-user key isolation should be added when the first endpoint is
# wired to accept API keys.
