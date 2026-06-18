from fastapi.testclient import TestClient


def test_register_success(client: TestClient) -> None:
    response = client.post("/auth/register", json={"email": "user@example.com", "password": "secret123"})
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "user@example.com"
    assert "id" in data
    assert "created_at" in data
    assert "password" not in data
    assert "hashed_password" not in data


def test_register_duplicate_email(client: TestClient) -> None:
    payload = {"email": "dupe@example.com", "password": "secret123"}
    client.post("/auth/register", json=payload)
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 409


def test_register_invalid_email(client: TestClient) -> None:
    response = client.post("/auth/register", json={"email": "not-an-email", "password": "secret123"})
    assert response.status_code == 422


def test_register_missing_password(client: TestClient) -> None:
    response = client.post("/auth/register", json={"email": "user@example.com"})
    assert response.status_code == 422


def test_register_missing_email(client: TestClient) -> None:
    response = client.post("/auth/register", json={"password": "secret123"})
    assert response.status_code == 422
