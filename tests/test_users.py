from fastapi.testclient import TestClient


def _register(client: TestClient, email: str = "user@example.com", password: str = "secret123") -> dict:
    return client.post("/auth/register", json={"email": email, "password": password}).json()


# --- GET /users/me ---

def test_get_me_returns_current_user(client: TestClient) -> None:
    tokens = _register(client)
    response = client.get("/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
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
    response = client.patch(
        "/users/me",
        json={"email": "new@example.com"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"id", "email", "created_at"}
    assert data["email"] == "new@example.com"


def test_update_email_to_taken_email(client: TestClient) -> None:
    _register(client, email="a@example.com")
    tokens_b = _register(client, email="b@example.com")
    response = client.patch(
        "/users/me",
        json={"email": "a@example.com"},
        headers={"Authorization": f"Bearer {tokens_b['access_token']}"},
    )
    assert response.status_code == 409


def test_update_password_correct_current(client: TestClient) -> None:
    tokens = _register(client)
    response = client.patch(
        "/users/me",
        json={"current_password": "secret123", "new_password": "newpassword1"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    login = client.post("/auth/login", json={"email": "user@example.com", "password": "newpassword1"})
    assert login.status_code == 200


def test_update_password_wrong_current(client: TestClient) -> None:
    tokens = _register(client)
    response = client.patch(
        "/users/me",
        json={"current_password": "wrongpassword", "new_password": "newpassword1"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 401


def test_update_unauthenticated(client: TestClient) -> None:
    response = client.patch("/users/me", json={"email": "new@example.com"})
    assert response.status_code == 401
