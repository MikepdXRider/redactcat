"""SQLAlchemy ORM models.

All models inherit from Base (app/database.py) and are imported by alembic/env.py
so Alembic can autogenerate migrations when they change. Add new models here;
never define them inside router or service files.
"""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_s3_key: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Plain int — no FK so the value survives Job deletion, preserving grouping for the client.
    job_id: Mapped[int | None] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String)
    input_type: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column()
    token_cost: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None), index=True
    )


# Composite index for the future enforcement query: WHERE user_id = ? AND created_at > ?
Index("ix_usage_events_user_created", UsageEvent.user_id, UsageEvent.created_at)
