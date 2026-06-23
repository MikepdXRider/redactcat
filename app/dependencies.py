"""Shared FastAPI dependencies.

`get_current_user` validates the Bearer JWT, resolves the User row, and raises
401 on any failure — any route that declares it as a Depends() is automatically
protected.

`get_current_user_any_auth` accepts either a JWT or a long-lived API key
(prefixed with rcat_). On API key requests it updates last_used_at before
returning the user, so usage attribution works identically to JWT auth.
"""

from datetime import UTC, datetime

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import ApiKey, User
from app.services.auth import API_KEY_PREFIX, hash_api_key

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
        api_key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(token)))
        if not api_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
        api_key.last_used_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()

        user = db.get(User, api_key.user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

        return user

    return _resolve_jwt(token, db)
