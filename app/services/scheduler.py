"""EventBridge Scheduler service.

Creates per-job one-time cleanup schedules that fire after JOB_TTL. Each schedule
invokes the expire_jobs Lambda with the job's S3 key; the Lambda deletes all objects
under the job prefix and the DB row.

JOB_TTL lives here (not in pdf.py) because it drives both the schedule window and the
TTL check in /pdf/redact.
"""

import json
from datetime import UTC, datetime, timedelta

import boto3

from app.config import settings

JOB_TTL = timedelta(hours=1)

_scheduler = boto3.client("scheduler")


def schedule_job_expiry(s3_key: str) -> None:
    token = s3_key.split("/")[2]
    # strftime strips tzinfo — AWS at() expressions are always interpreted as UTC
    fire_at = datetime.now(UTC).replace(tzinfo=None) + JOB_TTL
    _scheduler.create_schedule(
        Name=f"expire-{token}",
        ScheduleExpression=f"at({fire_at.strftime('%Y-%m-%dT%H:%M:%S')})",
        Target={
            "Arn": settings.EXPIRE_JOB_LAMBDA_ARN,
            "RoleArn": settings.SCHEDULER_EXECUTION_ROLE_ARN,
            "Input": json.dumps({"s3_key": s3_key}),
        },
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
    )
