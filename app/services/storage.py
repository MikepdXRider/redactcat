"""S3 storage helpers for ephemeral PDF file management.

Wraps boto3 S3 operations used by the PDF scan/redact flow. Files are short-lived:
originals are deleted after redaction; redacted outputs are deleted immediately after
the presigned URL is generated.
"""

import boto3


def upload_to_s3(data: bytes, bucket: str, key: str) -> None:
    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=data)


def download_from_s3(bucket: str, key: str) -> bytes:
    response = boto3.client("s3").get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def delete_from_s3(bucket: str, key: str) -> None:
    boto3.client("s3").delete_object(Bucket=bucket, Key=key)


def generate_presigned_url(bucket: str, key: str, ttl_seconds: int = 300) -> str:
    return boto3.client("s3").generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl_seconds,
    )
