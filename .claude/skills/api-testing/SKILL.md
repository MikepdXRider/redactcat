---
name: api-testing
description: Use when writing or reviewing pytest tests for this project's FastAPI endpoints. Encodes the required coverage matrix (auth, cross-user isolation, DTO shape, behavior-over-proxy) and the client-fixture conventions so endpoint tests are complete and fail on real regressions.
---

# API Testing Coverage

Write endpoint tests that **fail when the behavior is wrong** — not tests that pass because
something ran.

## Project conventions

**Auth:** Bearer token in `Authorization` header. Missing or invalid → `401`. Never `403` for a
missing header.

**Cross-user isolation:** Resources belonging to another user return `404`, not `403`. Do not
confirm that a resource exists to an unauthorized caller.

**Fixtures** (all in `tests/conftest.py`):
- `client` — `TestClient` with `get_db` overridden to an in-memory SQLite DB. Use for all HTTP assertions.
- `db` — `Session` bound to the same in-memory DB. Use **only** when the behavior you're testing
  is not surfaced by any endpoint response (e.g., a row was deleted, a cascade fired).
- `engine` — underlying engine; compose `db` and `client` from it. Don't use directly in tests.

**Seed helper:** Define a module-level `_register()` helper at the top of each test file to avoid
boilerplate. It creates a user and returns the token dict:

```python
def _register(client, email="user@example.com", password="secret123") -> dict:
    return client.post("/auth/register", json={"email": email, "password": password}).json()
```

---

## Two rules

1. **Assert the guarantee, not a proxy.** `len > 0` passes regardless of filtering.
   Exercise the real claim.
2. **Assert count AND content.** "Returns 3 items" passes for 3 *blank* items.

---

## Response shape

Pin the exact set of keys — catches both field leaks (password hash) and accidental drops:

```python
def test_exact_shape(client):
    tokens = _register(client)
    data = client.get("/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert set(data.keys()) == {"id", "email", "created_at"}  # not "in" — exact set
```

Never check for the absence of a sensitive field alone. A set assertion also catches its presence.

---

## Protected (authenticated) endpoints

```python
def test_requires_auth(client):
    assert client.get("/resource").status_code == 401

def test_rejects_invalid_token(client):
    assert client.get("/resource", headers={"Authorization": "Bearer not-valid"}).status_code == 401

def test_404_for_nonexistent(client):
    tokens = _register(client)
    assert client.get("/resource/999", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 404

def test_404_for_other_users_resource(client):  # 404, not 403 — do not confirm existence
    tokens_a = _register(client, email="a@example.com")
    tokens_b = _register(client, email="b@example.com")
    # Create a resource owned by user B, then attempt access as user A.
    # The exact creation step depends on the resource; the assertion is always 404.
```

---

## Input validation

Test the boundaries of every schema field, not just the happy path:

```python
def test_missing_required_field(client):
    assert client.post("/resource", json={}).status_code == 422

def test_invalid_format(client):
    assert client.post("/auth/register", json={"email": "not-an-email", "password": "secret123"}).status_code == 422

def test_below_min_length(client):
    assert client.post("/auth/register", json={"email": "a@b.com", "password": "short"}).status_code == 422
```

Login schemas intentionally omit `min_length` — wrong credentials return `401`, not `422`.

---

## Write endpoints (create / update / delete)

```python
def test_content_not_just_status(client):       # rule 2 — verify the data, not the count
    tokens = _register(client)
    data = client.post("/resource", json={"name": "test"}, headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert data["name"] == "test"

def test_conflict_on_duplicate(client):         # unique constraint → 409
    _register(client, email="dupe@example.com")
    r = client.post("/auth/register", json={"email": "dupe@example.com", "password": "secret123"})
    assert r.status_code == 409

def test_delete_returns_204(client):
    tokens = _register(client)
    r = client.delete("/resource/1", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 204
```

---

## Auth lifecycle

For any auth-stateful operation, test the full state machine — not just the success case:

```python
def test_logout_then_refresh_fails(client):     # revoked token can't be reused
    tokens = _register(client)
    client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401

def test_refresh_rotates_token(client):         # old token rejected after rotation
    tokens = _register(client)
    client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401

def test_expired_token_rejected(client, db):    # use db fixture to back-date expires_at
    tokens = _register(client)
    row = db.scalar(select(RefreshToken).where(RefreshToken.token == tokens["refresh_token"]))
    row.expires_at = datetime(2000, 1, 1)
    db.commit()
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
```

---

## Cascade and cleanup

When a delete should remove related rows, assert with the `db` fixture — the endpoint won't
surface the absence:

```python
def test_delete_purges_related_rows(client, db):
    tokens = _register(client)
    client.delete("/users/me", headers=auth(tokens))
    rows = db.scalars(select(RefreshToken)).all()
    assert rows == []
```

---

## DB-level assertions

Use the `db` fixture sparingly and deliberately:
- To verify a row was deleted that no endpoint returns
- To verify a cascade fired (e.g., related rows were cleaned up)
- To set up state that has no creation endpoint (e.g., back-dating `expires_at`)
- **Not** as a shortcut to avoid writing the correct endpoint test

---

## Also assert

- **Exact status code** — `201` vs `200`, `422` vs `400`, `204` vs `200`.
- **Ordering** when promised: `assert dates == sorted(dates, reverse=True)`.
- **Atomicity** — a multi-row write should roll back fully on mid-op failure; no partial writes.
- **Token persistence** — after register or login, assert the refresh token row exists in DB.

## Avoid

- Asserting only a status code, or a count without content.
- `x["field"] is None` as the *only* check — also passes when the key is **missing**;
  pair with a shape assert.
- Preserving a test that enshrines broken behavior — when fixing a bug, *flip* it.
- Testing the framework (Pydantic/FastAPI coercion) instead of your behavior.
- Reaching for `db` when an HTTP endpoint already surfaces the result.
- More than one behavioral guarantee per test.
