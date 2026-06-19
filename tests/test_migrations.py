# Integration tests for Alembic migrations — verifies upgrade, downgrade, and app behavior against a real file-based SQLite DB
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.main import app


def _alembic_config(db_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _sqlite_engine(db_url: str):
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    return engine


@contextmanager
def _client(db_url: str, raise_server_exceptions: bool = True):
    engine = _sqlite_engine(db_url)
    Session = sessionmaker(bind=engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app, raise_server_exceptions=raise_server_exceptions)
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_upgrade_creates_tables(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    command.upgrade(_alembic_config(db_url), "head")
    tables = inspect(create_engine(db_url)).get_table_names()
    assert "users" in tables
    assert "refresh_tokens" in tables
    assert "jobs" in tables


def test_downgrade_drops_tables(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    tables = inspect(create_engine(db_url)).get_table_names()
    assert "users" not in tables
    assert "refresh_tokens" not in tables
    assert "jobs" not in tables


def test_app_fails_without_migrations(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    with _client(db_url, raise_server_exceptions=False) as client:
        r = client.post("/auth/register", json={"email": "a@b.com", "password": "secret123"})
        assert r.status_code == 500


def test_app_works_after_migrations(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    command.upgrade(_alembic_config(db_url), "head")
    with _client(db_url) as client:
        r = client.post("/auth/register", json={"email": "a@b.com", "password": "secret123"})
        assert r.status_code == 201
        assert "access_token" in r.json()
