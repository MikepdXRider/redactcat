"""S3 storage helpers for ephemeral PDF file management.

Wraps boto3 S3 operations used by the PDF scan/redact flow. Originals are deleted
immediately after redaction. Redacted outputs are left in S3 for the client to
download via presigned URL; the bucket lifecycle rule (1-day expiration) cleans them up.
"""

import boto3

_s3 = boto3.client("s3")
_PRESIGNED_URL_TTL = 300  # 5 minutes — matches the spec in README and CLAUDE.md


def upload_to_s3(data: bytes, bucket: str, key: str) -> None:
    _s3.put_object(Bucket=bucket, Key=key, Body=data)


def download_from_s3(bucket: str, key: str) -> bytes:
    response = _s3.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def delete_from_s3(bucket: str, key: str) -> None:
    _s3.delete_object(Bucket=bucket, Key=key)


def generate_presigned_url(bucket: str, key: str, ttl_seconds: int = _PRESIGNED_URL_TTL) -> str:
    return _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_seconds,
    )
