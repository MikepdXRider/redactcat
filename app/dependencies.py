"""Shared FastAPI dependencies.

`get_current_user` validates the Bearer JWT, resolves the User row, and raises
401 on any failure — any route that declares it as a Depends() is automatically
protected.

`get_current_user_any_auth` accepts either a JWT or a long-lived API key
(prefixed with rcat_). On API key requests it updates last_used_at before
returning the user, so usage attribution works identically to JWT auth.

`enforce_token_limit` wraps `get_current_user_any_auth` and gates scan endpoints
by the current calendar month's token budget. Enforcement is at the scan step
only — redact endpoints are free and must not use this dependency.
"""

from datetime import UTC, date, datetime

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models import ApiKey, User, UsageEvent
from app.services.auth import API_KEY_PREFIX, hash_api_key
from app.services.usage import STRAY_TOKEN_LIMIT, billing_month_start

_bearer = HTTPBearer()


def _resolve_jwt(token: str, db: Session) -> User:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    return _resolve_jwt(credentials.credentials, db)


def get_current_user_any_auth(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials

    if token.startswith(API_KEY_PREFIX):
        api_key = db.scalar(
            select(ApiKey)
            .options(joinedload(ApiKey.user))
            .where(ApiKey.key_hash == hash_api_key(token))
        )
        if not api_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

        user = api_key.user
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

        api_key.last_used_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()

        return user

    return _resolve_jwt(token, db)


def enforce_token_limit(
    user: User = Depends(get_current_user_any_auth),
    db: Session = Depends(get_db),
) -> User:
    # Best-effort: concurrent requests can both pass before either records usage; acceptable at this scale.
    tokens_used = db.scalar(
        select(func.sum(UsageEvent.token_cost)).where(
            UsageEvent.user_id == user.id,
            UsageEvent.created_at >= billing_month_start(),
        )
    ) or 0
    if tokens_used >= STRAY_TOKEN_LIMIT:
        now = datetime.now(UTC).replace(tzinfo=None)
        next_month = now.month % 12 + 1
        next_year = now.year + (1 if now.month == 12 else 0)
        resets_in_days = (date(next_year, next_month, 1) - now.date()).days
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "token_limit_reached",
                "tokens_used": tokens_used,
                "tokens_allowed": STRAY_TOKEN_LIMIT,
                "resets_in_days": resets_in_days,
            },
        )
    return user
