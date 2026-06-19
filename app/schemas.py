from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

PASSWORD_MIN_LENGTH = 8


class HealthRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    current_password: str | None = None
    new_password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH)


class TextScanRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class DetectedEntity(BaseModel):
    entity_type: str
    text: str
    start_offset: int
    end_offset: int
    confidence: float


class TextScanRead(BaseModel):
    text: str
    entities: list[DetectedEntity]


class TextRedactRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    entities: list[DetectedEntity]
    replacement: str = "[REDACTED]"


class TextRedactRead(BaseModel):
    redacted_text: str
