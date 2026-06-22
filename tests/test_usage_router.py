# Tests for /usage endpoints — summary and history
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import UsageEvent


def _register(client: TestClient, email: str = "user@example.com", password: str = "secret123") -> dict:
    return client.post("/auth/register", json={"email": email, "password": password}).json()


def _auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _user_id(client: TestClient, tokens: dict) -> int:
    return client.get("/users/me", headers=_auth(tokens)).json()["id"]


# --- GET /usage/summary ---

def test_summary_requires_auth(client: TestClient) -> None:
    assert client.get("/usage/summary").status_code == 401


def test_summary_invalid_token(client: TestClient) -> None:
    assert client.get("/usage/summary", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_summary_shape(client: TestClient) -> None:
    tokens = _register(client)
    data = client.get("/usage/summary", headers=_auth(tokens)).json()
    assert set(data.keys()) == {"tokens_used", "reset_date"}


def test_summary_zero_when_no_events(client: TestClient) -> None:
    tokens = _register(client)
    data = client.get("/usage/summary", headers=_auth(tokens)).json()
    assert data["tokens_used"] == 0


def test_summary_sums_token_cost(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    uid = _user_id(client, tokens)
    db.add(UsageEvent(user_id=uid, event_type="COMPREHEND_CHAR", input_type="TEXT", quantity=100, token_cost=100))
    db.add(UsageEvent(user_id=uid, event_type="TEXTRACT_PAGE", input_type="PDF", quantity=1, token_cost=1500))
    db.commit()
    db.expire_all()
    data = client.get("/usage/summary", headers=_auth(tokens)).json()
    assert data["tokens_used"] == 1600


def test_summary_excludes_previous_month(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    uid = _user_id(client, tokens)
    db.add(UsageEvent(
        user_id=uid, event_type="COMPREHEND_CHAR", input_type="TEXT",
        quantity=100, token_cost=100, created_at=datetime(2000, 1, 1),
    ))
    db.commit()
    db.expire_all()
    data = client.get("/usage/summary", headers=_auth(tokens)).json()
    assert data["tokens_used"] == 0


def test_summary_excludes_other_users(client: TestClient, db: Session) -> None:
    tokens_a = _register(client, email="a@example.com")
    tokens_b = _register(client, email="b@example.com")
    uid_b = _user_id(client, tokens_b)
    db.add(UsageEvent(user_id=uid_b, event_type="COMPREHEND_CHAR", input_type="TEXT", quantity=500, token_cost=500))
    db.commit()
    db.expire_all()
    data = client.get("/usage/summary", headers=_auth(tokens_a)).json()
    assert data["tokens_used"] == 0


def test_summary_reset_date_is_first_of_next_month(client: TestClient) -> None:
    from datetime import date
    tokens = _register(client)
    data = client.get("/usage/summary", headers=_auth(tokens)).json()
    today = date.today()
    next_month = today.month % 12 + 1
    next_year = today.year + (1 if today.month == 12 else 0)
    assert data["reset_date"] == date(next_year, next_month, 1).isoformat()


# --- GET /usage/history ---

def test_history_requires_auth(client: TestClient) -> None:
    assert client.get("/usage/history").status_code == 401


def test_history_invalid_token(client: TestClient) -> None:
    assert client.get("/usage/history", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_history_empty_when_no_events(client: TestClient) -> None:
    tokens = _register(client)
    assert client.get("/usage/history", headers=_auth(tokens)).json() == []


def test_history_shape(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    uid = _user_id(client, tokens)
    db.add(UsageEvent(user_id=uid, event_type="COMPREHEND_CHAR", input_type="TEXT", quantity=100, token_cost=100))
    db.commit()
    db.expire_all()
    items = client.get("/usage/history", headers=_auth(tokens)).json()
    assert len(items) == 1
    assert set(items[0].keys()) == {"id", "event_type", "input_type", "quantity", "token_cost", "job_id", "created_at"}


def test_history_returns_current_month_events(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    uid = _user_id(client, tokens)
    db.add(UsageEvent(user_id=uid, event_type="COMPREHEND_CHAR", input_type="TEXT", quantity=100, token_cost=100))
    db.add(UsageEvent(user_id=uid, event_type="TEXTRACT_PAGE", input_type="PDF", quantity=1, token_cost=1500))
    db.commit()
    db.expire_all()
    items = client.get("/usage/history", headers=_auth(tokens)).json()
    assert len(items) == 2


def test_history_excludes_previous_month(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    uid = _user_id(client, tokens)
    db.add(UsageEvent(
        user_id=uid, event_type="COMPREHEND_CHAR", input_type="TEXT",
        quantity=100, token_cost=100, created_at=datetime(2000, 1, 1),
    ))
    db.commit()
    db.expire_all()
    assert client.get("/usage/history", headers=_auth(tokens)).json() == []


def test_history_excludes_other_users(client: TestClient, db: Session) -> None:
    tokens_a = _register(client, email="a@example.com")
    tokens_b = _register(client, email="b@example.com")
    uid_b = _user_id(client, tokens_b)
    db.add(UsageEvent(user_id=uid_b, event_type="COMPREHEND_CHAR", input_type="TEXT", quantity=500, token_cost=500))
    db.commit()
    db.expire_all()
    assert client.get("/usage/history", headers=_auth(tokens_a)).json() == []


def test_history_ordered_most_recent_first(client: TestClient, db: Session) -> None:
    tokens = _register(client)
    uid = _user_id(client, tokens)
    base = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    db.add(UsageEvent(
        user_id=uid, event_type="TEXTRACT_PAGE", input_type="PDF",
        quantity=1, token_cost=1500, created_at=base,
    ))
    db.add(UsageEvent(
        user_id=uid, event_type="COMPREHEND_CHAR", input_type="PDF",
        quantity=300, token_cost=300, created_at=base + timedelta(seconds=1),
    ))
    db.commit()
    db.expire_all()
    items = client.get("/usage/history", headers=_auth(tokens)).json()
    assert items[0]["event_type"] == "COMPREHEND_CHAR"
    assert items[1]["event_type"] == "TEXTRACT_PAGE"
