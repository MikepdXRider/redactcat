from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.modules.auth import hash_password, verify_password
from app.schemas import UserRead, UserUpdate

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
