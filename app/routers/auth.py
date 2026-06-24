"""Auth router.

Stateless JWTs (30min access token) paired with rotating opaque refresh tokens
stored in the DB. Each /refresh call replaces the old token row; /logout deletes
it. The access token expires naturally — no blacklist needed.
"""

import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import RefreshToken, User
from app.schemas import ErrorRead, RefreshRequest, TokenRead, UserCreate, UserLogin

router = APIRouter(tags=["auth"])


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: int) -> str:
    expire = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(user_id), "exp": expire}, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def store_refresh_token(user_id: int, db: Session) -> str:
    token = create_refresh_token()
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    db.add(RefreshToken(token=token, user_id=user_id, expires_at=expires_at))
    return token


@router.post(
    "/login",
    response_model=TokenRead,
    responses={401: {"model": ErrorRead, "description": "Invalid credentials."}},
)
def login(body: UserLogin, db: Session = Depends(get_db)) -> TokenRead:
    """Authenticate with email and password.

    Returns a JWT access token (valid 30 minutes) and a rotating refresh token.
    Store the refresh token and use `POST /auth/refresh` to obtain a new pair before
    the access token expires.
    """
    user = db.scalar(select(User).where(User.email == body.email))
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    refresh_token = store_refresh_token(user.id, db)
    db.commit()

    return TokenRead(
        access_token=create_access_token(user.id),
        refresh_token=refresh_token,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorRead, "description": "Invalid or expired access token."},
        404: {"model": ErrorRead, "description": "Refresh token not found or belongs to another user."},
    },
)
def logout(
    body: RefreshRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Invalidate a refresh token.

    Deletes the refresh token from the server. The access token expires naturally
    after its 30-minute window — no further action is needed.
    """
    row = db.scalar(
        select(RefreshToken).where(
            RefreshToken.token == body.refresh_token,
            RefreshToken.user_id == current_user.id,
        )
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/refresh",
    response_model=TokenRead,
    responses={401: {"model": ErrorRead, "description": "Invalid or expired refresh token."}},
)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> TokenRead:
    """Exchange a refresh token for a new token pair.

    The old refresh token is deleted and a new one is issued. Call this before the
    access token expires to maintain a session without re-entering credentials.
    Returns 401 if the refresh token is invalid or expired.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    row = db.scalar(select(RefreshToken).where(RefreshToken.token == body.refresh_token))
    if not row or row.expires_at < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    user_id = row.user_id
    db.delete(row)
    new_refresh_token = store_refresh_token(user_id, db)
    db.commit()

    return TokenRead(
        access_token=create_access_token(user_id),
        refresh_token=new_refresh_token,
    )


@router.post(
    "/register",
    response_model=TokenRead,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorRead, "description": "Email is already registered."}},
)
def register(body: UserCreate, db: Session = Depends(get_db)) -> TokenRead:
    """Create a new account and return a token pair.

    Returns the same token pair as `POST /auth/login`. Returns 409 if the email
    address is already associated with an account.
    """
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=body.email, hashed_password=hash_password(body.password))
    db.add(user)
    db.flush()

    refresh_token = store_refresh_token(user.id, db)
    db.commit()

    return TokenRead(
        access_token=create_access_token(user.id),
        refresh_token=refresh_token,
    )
