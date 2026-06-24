from fastapi.testclient import TestClient


def test_get_server_script_returns_200(client: TestClient) -> None:
    response = client.get("/mcp/server.py")
    assert response.status_code == 200


def test_get_server_script_content(client: TestClient) -> None:
    body = client.get("/mcp/server.py").text
    assert "FastMCP" in body
    assert "redactcat" in body


def test_get_install_script_returns_200(client: TestClient) -> None:
    response = client.get("/mcp/install.sh")
    assert response.status_code == 200


def test_get_install_script_content(client: TestClient) -> None:
    body = client.get("/mcp/install.sh").text
    assert "api.redactcat.com" in body
    assert "REDACTCAT_API_KEY" in body


def test_mcp_endpoints_require_no_auth(client: TestClient) -> None:
    assert client.get("/mcp/server.py").status_code == 200
    assert client.get("/mcp/install.sh").status_code == 200
