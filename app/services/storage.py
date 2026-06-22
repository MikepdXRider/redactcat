"""S3 storage helpers for ephemeral PDF file management.

Wraps boto3 S3 operations used by the PDF scan/redact flow. All S3 cleanup
(originals and redacted versions) is handled by the per-job expiry Lambda, which
deletes everything under the job prefix ~1 hour after scan. The bucket lifecycle
rule (1-day expiration) is a fallback for any objects the Lambda misses.
"""

import boto3

_s3 = boto3.client("s3")
_PRESIGNED_URL_TTL = 3600  # matches JOB_TTL so expires_at is an accurate expiry for both the URL and the S3 objects


def upload_to_s3(data: bytes, bucket: str, key: str) -> None:
    _s3.put_object(Bucket=bucket, Key=key, Body=data)


def download_from_s3(bucket: str, key: str) -> bytes:
    response = _s3.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def generate_presigned_url(bucket: str, key: str, ttl_seconds: int = _PRESIGNED_URL_TTL) -> str:
    return _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_seconds,
    )
