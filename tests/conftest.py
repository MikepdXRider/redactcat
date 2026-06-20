# Pytest fixtures — in-memory SQLite engine, db session, and TestClient shared across the test suite
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

_TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture
def engine():
    # StaticPool forces all connections to reuse one underlying connection so
    # tables created by create_all are visible to sessions opened later.
    _engine = create_engine(
        _TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(_engine, "connect")
    def set_sqlite_pragma(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(bind=_engine)
    yield _engine
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture
def db(engine) -> Session:
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _make_client(engine, raise_server_exceptions: bool = True) -> TestClient:
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        _db = TestingSessionLocal()
        try:
            yield _db
        finally:
            _db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture
def client(engine) -> TestClient:
    with _make_client(engine) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def client_no_raise(engine) -> TestClient:
    """TestClient that converts unhandled server exceptions to 500 responses instead of re-raising."""
    with _make_client(engine, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()
