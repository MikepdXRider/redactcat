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
from app.schemas import ApiKeyMetadataRead, ApiKeyRead, ErrorRead, UserRead, UserUpdate
from app.services.auth import API_KEY_PREFIX, KEY_PREFIX_DISPLAY_CHARS, hash_api_key

router = APIRouter(tags=["users"])


@router.get(
    "/me",
    response_model=UserRead,
    responses={401: {"model": ErrorRead, "description": "Invalid or expired token."}},
)
def get_me(current_user: User = Depends(get_current_user)) -> User:
    """Return the authenticated user's profile."""
    return current_user


@router.patch(
    "/me",
    response_model=UserRead,
    responses={
        401: {"model": ErrorRead, "description": "Invalid token or incorrect current password."},
        409: {"model": ErrorRead, "description": "New email is already registered."},
    },
)
def update_me(
    body: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Update email or password.

    All fields are optional. To change the password, `current_password` must be
    provided and correct — returns 401 otherwise. Returns 409 if the new email
    is already in use.
    """
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


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: {"model": ErrorRead, "description": "Invalid or expired token."}},
)
def delete_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Permanently delete the account and all associated data.

    Revokes all refresh tokens and API keys before deleting the user record.
    **This action is irreversible.**
    """
    db.execute(delete(RefreshToken).where(RefreshToken.user_id == current_user.id))
    db.delete(current_user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/me/api-key",
    response_model=ApiKeyRead,
    status_code=status.HTTP_201_CREATED,
    responses={401: {"model": ErrorRead, "description": "Invalid or expired token."}},
)
def generate_or_rotate_api_key(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiKeyRead:
    """Generate or rotate the account's long-lived API key.

    If a key already exists it is immediately revoked and replaced. **The raw key
    is returned once and never stored — save it immediately.** Pass the key as
    `Authorization: Bearer <key>` on `/text/*` and `/pdf/*` endpoints.
    """
    raw = secrets.token_urlsafe(32)
    full_key = API_KEY_PREFIX + raw
    key_prefix = API_KEY_PREFIX + raw[:KEY_PREFIX_DISPLAY_CHARS]
    db.execute(delete(ApiKey).where(ApiKey.user_id == current_user.id))
    db.add(ApiKey(user_id=current_user.id, key_hash=hash_api_key(full_key), key_prefix=key_prefix))
    db.commit()
    return ApiKeyRead(key=full_key)


@router.get(
    "/me/api-key",
    response_model=ApiKeyMetadataRead,
    responses={
        401: {"model": ErrorRead, "description": "Invalid or expired token."},
        404: {"model": ErrorRead, "description": "No API key is active for this account."},
    },
)
def get_api_key_metadata(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiKeyMetadataRead:
    """Return metadata about the current API key.

    Returns the key prefix (for identification) and usage timestamps. The raw key
    is not returned — if lost, rotate the key with `POST /users/me/api-key`.
    Returns 404 if no key is active.
    """
    api_key = db.scalar(select(ApiKey).where(ApiKey.user_id == current_user.id))
    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return ApiKeyMetadataRead(
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
    )


@router.delete(
    "/me/api-key",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: {"model": ErrorRead, "description": "Invalid or expired token."}},
)
def revoke_api_key(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Revoke the current API key immediately.

    Any in-flight requests using the key will fail with 401 after revocation.
    No error is returned if no key is active.
    """
    db.execute(delete(ApiKey).where(ApiKey.user_id == current_user.id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
