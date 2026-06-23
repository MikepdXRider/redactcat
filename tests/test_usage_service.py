from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UsageEvent, User
from app.schemas import EventType, InputType
from app.services.usage import TOKEN_COST_PER_UNIT, record_usage_event


def _seed_user(db: Session, email: str = "test@example.com") -> User:
    user = User(email=email, hashed_password="hash")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_record_usage_event_persists_all_fields(db: Session) -> None:
    user = _seed_user(db)
    record_usage_event(db, user.id, EventType.TEXTRACT_PAGE, InputType.PDF, quantity=1, job_id=42)

    db.expire_all()
    ev = db.scalars(select(UsageEvent)).one()
    assert ev.user_id == user.id
    assert ev.job_id == 42
    assert ev.event_type == EventType.TEXTRACT_PAGE
    assert ev.input_type == InputType.PDF
    assert ev.quantity == 1
    assert ev.token_cost == TOKEN_COST_PER_UNIT[EventType.TEXTRACT_PAGE]
    assert ev.created_at is not None


def test_record_usage_event_token_cost_computed_from_quantity(db: Session) -> None:
    user = _seed_user(db)
    quantity = 5000
    record_usage_event(db, user.id, EventType.COMPREHEND_CHAR, InputType.TEXT, quantity=quantity)

    db.expire_all()
    ev = db.scalars(select(UsageEvent)).one()
    assert ev.quantity == quantity
    assert ev.token_cost == quantity * TOKEN_COST_PER_UNIT[EventType.COMPREHEND_CHAR]


def test_record_usage_event_zero_cost_for_redaction_events(db: Session) -> None:
    user = _seed_user(db)
    record_usage_event(db, user.id, EventType.PDF_REDACTION, InputType.PDF, quantity=1)
    record_usage_event(db, user.id, EventType.TEXT_REDACTION, InputType.TEXT, quantity=1)

    db.expire_all()
    events = db.scalars(select(UsageEvent)).all()
    assert len(events) == 2
    assert all(ev.token_cost == 0 for ev in events)


def test_record_usage_event_null_job_id(db: Session) -> None:
    user = _seed_user(db)
    record_usage_event(db, user.id, EventType.TEXT_REDACTION, InputType.TEXT, quantity=1)

    db.expire_all()
    ev = db.scalars(select(UsageEvent)).one()
    assert ev.job_id is None


def test_record_usage_event_job_id_stored_even_after_job_would_be_deleted(db: Session) -> None:
    # job_id is a plain int with no FK — it survives independently of any Job row
    user = _seed_user(db)
    record_usage_event(db, user.id, EventType.TEXTRACT_PAGE, InputType.PDF, quantity=1, job_id=9999)

    db.expire_all()
    ev = db.scalars(select(UsageEvent)).one()
    assert ev.job_id == 9999


def test_record_usage_event_attributes_to_correct_user(db: Session) -> None:
    user_a = _seed_user(db, email="a@example.com")
    user_b = _seed_user(db, email="b@example.com")
    record_usage_event(db, user_a.id, EventType.COMPREHEND_CHAR, InputType.TEXT, quantity=100)

    db.expire_all()
    events = db.scalars(select(UsageEvent)).all()
    assert len(events) == 1
    assert events[0].user_id == user_a.id
    assert events[0].user_id != user_b.id


def test_record_usage_event_db_failure_does_not_raise(db: Session) -> None:
    user = _seed_user(db)
    with patch.object(db, "add", side_effect=Exception("DB error")):
        record_usage_event(db, user.id, EventType.TEXTRACT_PAGE, InputType.PDF, quantity=1)

    db.expire_all()
    assert db.scalars(select(UsageEvent)).all() == []
