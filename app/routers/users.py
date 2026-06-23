"""Users router.

All endpoints require get_current_user. DELETE /me explicitly purges refresh
tokens before deleting the user row; the FK cascade on refresh_tokens.user_id
is a safety net.

API key management lives here because an API key is a user credential resource,
not an auth flow. Only JWTs are accepted by these endpoints — a key cannot
manage itself.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import ApiKey, RefreshToken, User
from app.routers.auth import hash_password, verify_password
from app.schemas import ApiKeyMetadataRead, ApiKeyRead, UserRead, UserUpdate
from app.services.auth import API_KEY_PREFIX, KEY_PREFIX_DISPLAY_CHARS, hash_api_key

router = APIRouter(tags=["users"])


@router.get("/me", response_model=UserRead)
def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.patch("/me", response_model=UserRead)
def update_me(
    body: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if body.email is not None and body.email != current_user.email:
        if db.scalar(select(User).where(User.email == body.email)):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
        current_user.email = body.email

    if body.new_password is not None:
        if not body.current_password or not verify_password(body.current_password, current_user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
        current_user.hashed_password = hash_password(body.new_password)

    db.commit()
    db.refresh(current_user)
    return current_user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    db.execute(delete(RefreshToken).where(RefreshToken.user_id == current_user.id))
    db.delete(current_user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/api-key", response_model=ApiKeyRead, status_code=status.HTTP_201_CREATED)
def generate_or_rotate_api_key(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiKeyRead:
    raw = secrets.token_urlsafe(32)
    full_key = API_KEY_PREFIX + raw
    key_prefix = API_KEY_PREFIX + raw[:KEY_PREFIX_DISPLAY_CHARS]
    db.execute(delete(ApiKey).where(ApiKey.user_id == current_user.id))
    db.add(ApiKey(user_id=current_user.id, key_hash=hash_api_key(full_key), key_prefix=key_prefix))
    db.commit()
    return ApiKeyRead(key=full_key)


@router.get("/me/api-key", response_model=ApiKeyMetadataRead)
def get_api_key_metadata(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiKeyMetadataRead:
    api_key = db.scalar(select(ApiKey).where(ApiKey.user_id == current_user.id))
    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return ApiKeyMetadataRead(
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
    )


@router.delete("/me/api-key", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    db.execute(delete(ApiKey).where(ApiKey.user_id == current_user.id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
