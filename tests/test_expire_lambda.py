from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

import expire_jobs as lambda_module
from expire_jobs import handler

S3_KEY = "pdfs/42/AbCdEfGhIjKlMnOp/original.pdf"
PREFIX = "pdfs/42/AbCdEfGhIjKlMnOp/"
BUCKET = "test-bucket"
DATABASE_URL = "postgresql://user:pass@host/db"
SSM_PATH = "/redactcat/DATABASE_URL"


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("DATABASE_URL_SSM_PATH", SSM_PATH)


@pytest.fixture
def mock_s3(monkeypatch):
    mock = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"Contents": [{"Key": f"{PREFIX}original.pdf"}, {"Key": f"{PREFIX}redacted_abc.pdf"}]},
    ]
    mock.get_paginator.return_value = paginator
    monkeypatch.setattr(lambda_module, "_s3", mock)
    return mock


@pytest.fixture
def mock_ssm(monkeypatch):
    mock = MagicMock()
    mock.get_parameter.return_value = {"Parameter": {"Value": DATABASE_URL}}
    monkeypatch.setattr(lambda_module, "_ssm", mock)
    return mock


@pytest.fixture
def mock_psycopg2(monkeypatch):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.rowcount = 1
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    mock_connect = MagicMock(return_value=mock_conn)
    monkeypatch.setattr(lambda_module.psycopg2, "connect", mock_connect)
    return mock_connect, mock_cur


def test_deletes_all_objects_under_prefix(mock_s3, mock_ssm, mock_psycopg2):
    handler({"s3_key": S3_KEY}, None)

    mock_s3.get_paginator.assert_called_once_with("list_objects_v2")
    mock_s3.get_paginator.return_value.paginate.assert_called_once_with(Bucket=BUCKET, Prefix=PREFIX)
    assert mock_s3.delete_object.call_count == 2
    mock_s3.delete_object.assert_any_call(Bucket=BUCKET, Key=f"{PREFIX}original.pdf")
    mock_s3.delete_object.assert_any_call(Bucket=BUCKET, Key=f"{PREFIX}redacted_abc.pdf")


def test_reads_database_url_from_ssm(mock_s3, mock_ssm, mock_psycopg2):
    handler({"s3_key": S3_KEY}, None)

    mock_ssm.get_parameter.assert_called_once_with(Name=SSM_PATH, WithDecryption=True)


def test_deletes_job_row_by_s3_key(mock_s3, mock_ssm, mock_psycopg2):
    _, mock_cur = mock_psycopg2
    handler({"s3_key": S3_KEY}, None)

    mock_cur.execute.assert_called_once_with(
        "DELETE FROM jobs WHERE original_s3_key = %s", (S3_KEY,)
    )


def test_s3_delete_error_on_one_key_continues_to_next(mock_s3, mock_ssm, mock_psycopg2):
    mock_s3.delete_object.side_effect = [
        ClientError({"Error": {"Code": "NoSuchKey", "Message": ""}}, "DeleteObject"),
        None,
    ]

    handler({"s3_key": S3_KEY}, None)

    assert mock_s3.delete_object.call_count == 2


def test_db_exception_is_swallowed_after_s3_cleanup(mock_s3, mock_ssm, monkeypatch):
    monkeypatch.setattr(lambda_module.psycopg2, "connect", MagicMock(side_effect=Exception("conn failed")))

    # Should not raise — DB cleanup is best-effort
    handler({"s3_key": S3_KEY}, None)

    # S3 cleanup still ran
    assert mock_s3.delete_object.call_count == 2


def test_missing_s3_key_raises():
    with pytest.raises(KeyError):
        handler({}, None)


def test_empty_prefix_no_objects(mock_s3, mock_ssm, mock_psycopg2):
    mock_s3.get_paginator.return_value.paginate.return_value = [{"Contents": []}]

    handler({"s3_key": S3_KEY}, None)

    mock_s3.delete_object.assert_not_called()


def test_page_with_no_contents_key(mock_s3, mock_ssm, mock_psycopg2):
    mock_s3.get_paginator.return_value.paginate.return_value = [{}]

    handler({"s3_key": S3_KEY}, None)

    mock_s3.delete_object.assert_not_called()


def test_s3_list_error_propagates(mock_s3, mock_ssm, mock_psycopg2):
    mock_s3.get_paginator.return_value.paginate.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": ""}}, "ListObjectsV2"
    )

    with pytest.raises(ClientError):
        handler({"s3_key": S3_KEY}, None)
