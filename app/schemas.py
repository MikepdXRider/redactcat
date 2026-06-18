from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class HealthRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
