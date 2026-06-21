"""Pydantic schemas enforced at request deserialization and response serialization.

The API's type contracts. Convention: XRead (response), XCreate/XRequest (request
body), XUpdate (partial), XLogin (auth input — no min_length so wrong credentials
return 401 not 422). Shared validation constants (e.g. PASSWORD_MIN_LENGTH) are
defined once here and referenced by name.
"""

from datetime import datetime
from typing import Literal

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


class TokenRead(BaseModel):
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


class BoundingBox(BaseModel):
    left: float
    top: float
    width: float
    height: float


class PdfEntityRead(DetectedEntity):
    source: Literal["COMPREHEND", "REKOGNITION"]
    bboxes: list[BoundingBox]


class PdfScanRead(BaseModel):
    job_id: int
    entities: list[PdfEntityRead]


class PdfRedactRequest(BaseModel):
    job_id: int
    entities: list[PdfEntityRead]


class PdfRedactRead(BaseModel):
    download_url: str
