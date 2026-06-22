import json
from unittest.mock import patch

import pytest

import app.services.scheduler as scheduler_module
from app.services.scheduler import JOB_TTL, schedule_job_expiry

TEST_S3_KEY = "pdfs/42/AbCdEfGhIjKlMnOp/original.pdf"
TEST_TOKEN = "AbCdEfGhIjKlMnOp"
FAKE_LAMBDA_ARN = "arn:aws:lambda:us-west-2:123456789012:function:expire"
FAKE_ROLE_ARN = "arn:aws:iam::123456789012:role/scheduler-execution"


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    monkeypatch.setattr(scheduler_module.settings, "EXPIRE_JOB_LAMBDA_ARN", FAKE_LAMBDA_ARN)
    monkeypatch.setattr(scheduler_module.settings, "SCHEDULER_EXECUTION_ROLE_ARN", FAKE_ROLE_ARN)


def test_schedule_job_expiry_sends_correct_request():
    with patch.object(scheduler_module._scheduler, "create_schedule", return_value={}) as mock_create:
        schedule_job_expiry(TEST_S3_KEY)

    mock_create.assert_called_once()
    kwargs = mock_create.call_args.kwargs

    assert kwargs["Name"] == f"expire-{TEST_TOKEN}"
    assert kwargs["ScheduleExpression"].startswith("at(")
    assert kwargs["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert kwargs["ActionAfterCompletion"] == "DELETE"
    assert kwargs["Target"]["Arn"] == FAKE_LAMBDA_ARN
    assert kwargs["Target"]["RoleArn"] == FAKE_ROLE_ARN
    payload = json.loads(kwargs["Target"]["Input"])
    assert payload == {"s3_key": TEST_S3_KEY}


def test_schedule_name_uses_token_segment():
    with patch.object(scheduler_module._scheduler, "create_schedule", return_value={}) as mock_create:
        schedule_job_expiry("pdfs/99/ZzZzZzZzZzZzZzZz/original.pdf")

    assert mock_create.call_args.kwargs["Name"] == "expire-ZzZzZzZzZzZzZzZz"


def test_job_ttl_is_one_hour():
    from datetime import timedelta
    assert JOB_TTL == timedelta(hours=1)
