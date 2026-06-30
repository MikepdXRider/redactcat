"""Pydantic schemas enforced at request deserialization and response serialization.

The API's type contracts. Convention: XRead (response), XCreate/XRequest (request
body), XUpdate (partial), XLogin (auth input — no min_length so wrong credentials
return 401 not 422). Shared validation constants (e.g. PASSWORD_MIN_LENGTH) are
defined once here and referenced by name.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

PASSWORD_MIN_LENGTH = 16

# Floats in scan responses (bbox coordinates, confidence scores) are trimmed to 4
# decimal places. Textract/Comprehend precision beyond that is noise — sub-pixel on
# any page dimension — and the extra digits inflate MCP payloads significantly.
def _round4(v: float) -> float:
    return round(v, 4)


_RoundedFloat = Annotated[float, AfterValidator(_round4)]


class HealthRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str = Field(description="Always 'ok' when the service is healthy.")


class UserCreate(BaseModel):
    email: EmailStr = Field(description="Email address for the new account.")
    password: str = Field(min_length=PASSWORD_MIN_LENGTH)


class UserLogin(BaseModel):
    email: EmailStr = Field(description="Registered email address.")
    password: str


# StrEnum members compare and serialize as their string value, so the wire
# representation is identical to str — no migration or response shape change.
class EventType(StrEnum):
    """AWS service call that generated the usage event.

    - `TEXTRACT_PAGE`: one page processed by Amazon Textract (1,500 tokens)
    - `COMPREHEND_CHAR`: one character analyzed by Amazon Comprehend (1 token; min 300 chars billed)
    - `REKOGNITION_FACE`: one image analyzed by Amazon Rekognition for faces (1,000 tokens)
    - `PDF_REDACTION`: a PDF redaction call (0 tokens)
    - `TEXT_REDACTION`: a text redaction call (0 tokens)
    - `IMAGE_REDACTION`: an image redaction call (0 tokens)
    """

    TEXTRACT_PAGE = "TEXTRACT_PAGE"
    COMPREHEND_CHAR = "COMPREHEND_CHAR"
    REKOGNITION_FACE = "REKOGNITION_FACE"
    PDF_REDACTION = "PDF_REDACTION"
    TEXT_REDACTION = "TEXT_REDACTION"
    IMAGE_REDACTION = "IMAGE_REDACTION"


class InputType(StrEnum):
    PDF = "PDF"
    TEXT = "TEXT"
    IMAGE = "IMAGE"


class UsageRead(BaseModel):
    tokens_used: int = Field(ge=0, description="Tokens consumed so far in the current billing period.")
    tokens_allowed: int = Field(ge=0, description="Monthly token budget for this account.")
    tokens_remaining: int = Field(ge=0, description="Tokens remaining before scan endpoints return 429.")
    reset_date: date = Field(description="Date the billing period resets (first of the month, UTC).", examples=["2026-07-01"])


class UsageEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Internal event ID.")
    event_type: EventType = Field(description="The AWS service call that generated this event.")
    input_type: InputType = Field(description="Whether the input was a PDF, plain text, or image.")
    quantity: int = Field(ge=0, description="Units consumed (characters for Comprehend, pages for Textract, image count for Rekognition).")
    token_cost: int = Field(ge=0, description="Tokens charged for this event (`quantity × cost_per_unit`; 0 for redaction events).")
    job_id: int | None = Field(description="ID of the PDF job this event belongs to. Null for text events.")
    created_at: datetime = Field(description="UTC timestamp of the event (ISO 8601).", examples=["2026-06-23T14:30:00"])


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime = Field(description="UTC timestamp when the account was created (ISO 8601).", examples=["2026-06-01T10:00:00"])


class TokenRead(BaseModel):
    access_token: str = Field(description="JWT access token. Valid for 30 minutes. Pass as `Authorization: Bearer <token>`.")
    refresh_token: str = Field(description="Opaque refresh token for `POST /auth/refresh`. Rotated on each use.")
    token_type: str = Field(default="bearer", description="Always 'bearer'.")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(description="The refresh token from a previous login or refresh response.")


class UserUpdate(BaseModel):
    email: EmailStr | None = Field(default=None, description="New email address. Returns 409 if already in use.")
    current_password: str | None = Field(default=None, description="Current password. Required when setting `new_password`.")
    new_password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH, description="Requires `current_password`.")


class TextScanRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000, description="Plain text to scan for PII.")


class DetectedEntity(BaseModel):
    entity_type: str = Field(description="PII category (e.g. `NAME`, `EMAIL`, `PHONE`, `SSN`, `ADDRESS`).", examples=["EMAIL"])
    text: str = Field(description="The exact text identified as PII.", examples=["alice@example.com"])
    start_offset: int = Field(ge=0, description="Zero-indexed character offset of the first character of this entity in the original text.")
    end_offset: int = Field(ge=0, description="Character offset one past the last character of this entity (exclusive).")
    confidence: _RoundedFloat = Field(ge=0.0, le=1.0, description="Detection confidence score.", examples=[0.9998])


class TextScanRead(BaseModel):
    text: str = Field(description="The original text that was submitted.")
    entities: list[DetectedEntity] = Field(description="Detected PII entities. Pass this list (or a filtered subset) directly to `POST /text/redact`.")


class TextRedactRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000, description="The original text to redact.")
    entities: list[DetectedEntity] = Field(description="Entities to redact. Pass the scan response or a filtered subset — only the provided entities are redacted.")
    replacement: str = Field(default="[REDACTED]", description="String substituted for each redacted entity. Defaults to `[REDACTED]`. Pass an empty string to delete PII without a placeholder.")


class TextRedactRead(BaseModel):
    redacted_text: str = Field(description="The original text with each detected entity replaced by `replacement`.")


class BoundingBox(BaseModel):
    left: _RoundedFloat = Field(ge=0.0, le=1.0, description="Left edge as a fraction of page width (0.0–1.0).")
    top: _RoundedFloat = Field(ge=0.0, le=1.0, description="Top edge as a fraction of page height (0.0–1.0).")
    width: _RoundedFloat = Field(ge=0.0, le=1.0, description="Width as a fraction of page width (0.0–1.0).")
    height: _RoundedFloat = Field(ge=0.0, le=1.0, description="Height as a fraction of page height (0.0–1.0).")


# Which detector produced a PdfEntityRead. The field is typed as this enum, so the
# valid set lives in one place and any reference (router, tests) is a guaranteed
# member. StrEnum members compare and serialize as their string value.
class EntitySource(StrEnum):
    """Which detector produced the entity.

    - `COMPREHEND`: text PII detected by Amazon Comprehend
    - `REKOGNITION`: a face detected by Amazon Rekognition (only present when the page contains embedded raster images)
    - `PYZBAR`: a QR code or barcode decoded by pyzbar
    """

    COMPREHEND = "COMPREHEND"
    REKOGNITION = "REKOGNITION"
    PYZBAR = "PYZBAR"


class PdfEntityRead(DetectedEntity):
    source: EntitySource = Field(description="Which detector found this entity: `COMPREHEND` (text PII), `REKOGNITION` (face), or `PYZBAR` (QR/barcode).")
    bboxes: list[BoundingBox] = Field(description="Page-relative bounding boxes in normalized coordinates. One box per word for text entities; one box for faces and barcodes.")


class PdfScanRead(BaseModel):
    job_id: int = Field(description="Job identifier. Pass to `POST /pdf/redact` to apply redactions within the TTL window.")
    entities: list[PdfEntityRead] = Field(description="Detected entities with bounding boxes. Pass this list (or a filtered subset) to `POST /pdf/redact`.")
    expires_at: datetime = Field(description="UTC datetime when the job and original PDF are deleted (approximately 1 hour after scan, ISO 8601).", examples=["2026-06-23T15:30:00"])


class PdfRedactRequest(BaseModel):
    job_id: int = Field(description="The `job_id` returned by `POST /pdf/scan`.")
    entities: list[PdfEntityRead] = Field(description="Entities to redact. Pass the scan response or a filtered subset.")


class PdfRedactRead(BaseModel):
    download_url: str = Field(description="Pre-signed S3 URL for the redacted PDF. Valid until `expires_at` — download promptly.")
    expires_at: datetime = Field(description="UTC datetime when the download URL and job expire (ISO 8601).", examples=["2026-06-23T15:30:00"])


class ImageEntityRead(PdfEntityRead):
    source: EntitySource = Field(description="Which detector found this entity: `COMPREHEND` (text PII) or `REKOGNITION` (face).")
    bboxes: list[BoundingBox] = Field(description="Image-relative bounding boxes in normalized coordinates. One box per word for text entities; one box for faces.")


class ImageScanRead(BaseModel):
    job_id: int = Field(description="Job identifier. Pass to `POST /image/redact` to apply redactions within the TTL window.")
    entities: list[ImageEntityRead] = Field(description="Detected entities with bounding boxes. Pass this list (or a filtered subset) to `POST /image/redact`.")
    expires_at: datetime = Field(description="UTC datetime when the job and original image are deleted (approximately 1 hour after scan, ISO 8601).", examples=["2026-06-23T15:30:00"])


class ImageRedactRequest(BaseModel):
    job_id: int = Field(description="The `job_id` returned by `POST /image/scan`.")
    entities: list[ImageEntityRead] = Field(description="Entities to redact. Pass the scan response or a filtered subset.")


class ImageRedactRead(BaseModel):
    download_url: str = Field(description="Pre-signed S3 URL for the redacted image. Valid until `expires_at` — download promptly.")
    expires_at: datetime = Field(description="UTC datetime when the download URL and job expire (ISO 8601).", examples=["2026-06-23T15:30:00"])


class ApiKeyRead(BaseModel):
    key: str = Field(description="Full API key, prefixed with `rcat_`. **Shown only once — store it immediately.** Pass as `Authorization: Bearer <key>` on `/text/*` and `/pdf/*` endpoints.")


class ApiKeyMetadataRead(BaseModel):
    key_prefix: str = Field(description="First few characters of the key for identification. The full key is not stored.", examples=["rcat_abc123"])
    created_at: datetime = Field(description="UTC timestamp when the key was generated or last rotated (ISO 8601).", examples=["2026-06-01T10:00:00"])
    last_used_at: datetime | None = Field(description="UTC timestamp of the most recent authenticated request using this key. Null if the key has never been used (ISO 8601).", examples=["2026-06-23T14:00:00"])


class ErrorRead(BaseModel):
    detail: str = Field(description="Human-readable error message.")


class TokenLimitDetailRead(BaseModel):
    error: str = Field(description="Always 'token_limit_reached'.", examples=["token_limit_reached"])
    tokens_used: int = Field(ge=0, description="Tokens consumed in the current billing period.")
    tokens_allowed: int = Field(ge=0, description="Monthly token budget.")
    resets_in_days: int = Field(ge=0, description="Days until the budget resets on the 1st of the next month.")


class TokenLimitErrorRead(BaseModel):
    detail: TokenLimitDetailRead
