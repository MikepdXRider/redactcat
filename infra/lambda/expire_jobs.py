"""Per-job cleanup Lambda.

Triggered by EventBridge Scheduler ~1 hour after a PDF job is created. Deletes all
S3 objects under the job prefix (original PDF + any redacted versions) and removes the
Job DB row. Scheduled immediately after S3 upload so orphaned objects are cleaned up
even if the Job row was never committed.
"""

import logging
import os

import boto3
import psycopg2
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_s3 = boto3.client("s3")
_ssm = boto3.client("ssm")


def handler(event: dict, context: object) -> None:
    s3_key: str = event["s3_key"]  # KeyError → Lambda fails visibly so EventBridge records the failure
    s3_bucket = os.environ["S3_BUCKET"]
    database_url_ssm_path = os.environ["DATABASE_URL_SSM_PATH"]
    prefix = s3_key.rsplit("/", 1)[0] + "/"

    # S3 cleanup — PII concern; raise if listing fails; log and continue on per-key delete errors
    token = s3_key.split("/")[2]

    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=s3_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            filename = obj["Key"].rsplit("/", 1)[-1]
            try:
                _s3.delete_object(Bucket=s3_bucket, Key=obj["Key"])
                logger.info("deleted token=%s file=%s", token, filename)
            except ClientError as exc:
                logger.error("failed to delete token=%s file=%s: %s", token, filename, exc)

    # DB cleanup — best-effort; Job row contains no PII
    try:
        response = _ssm.get_parameter(Name=database_url_ssm_path, WithDecryption=True)
        database_url = response["Parameter"]["Value"]
        with psycopg2.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM jobs WHERE original_s3_key = %s", (s3_key,))
                logger.info("deleted %d job row(s) for token=%s", cur.rowcount, token)
    except Exception as exc:
        logger.error("db cleanup failed for token=%s: %s", token, type(exc).__name__)
