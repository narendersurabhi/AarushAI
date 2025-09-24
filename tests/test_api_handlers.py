import base64
import base64
import json
from unittest.mock import MagicMock

import pytest

from src.lambdas.api_handlers import app as api_app


@pytest.fixture(autouse=True)
def stub_clients(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET_NAME", "upload-bucket")
    monkeypatch.setenv("ARTIFACT_BUCKET_NAME", "artifact-bucket")
    monkeypatch.setenv("JOB_TABLE_NAME", "job-table")
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:stateMachine")

    api_app.UPLOAD_BUCKET = "upload-bucket"
    api_app.ARTIFACT_BUCKET = "artifact-bucket"
    api_app.JOB_TABLE = "job-table"
    api_app.STATE_MACHINE_ARN = "arn:stateMachine"

    s3_client = MagicMock()
    ddb_client = MagicMock()
    sfn_client = MagicMock()
    sfn_client.start_execution.return_value = {"executionArn": "arn:execution"}

    def fake_client(name, *args, **kwargs):
        if name == "s3":
            return s3_client
        if name == "dynamodb":
            return ddb_client
        if name == "stepfunctions":
            return sfn_client
        raise AssertionError(f"unexpected client {name}")

    monkeypatch.setattr(api_app, "s3_client", s3_client)
    monkeypatch.setattr(api_app, "ddb_client", ddb_client)
    monkeypatch.setattr(api_app, "sfn_client", sfn_client)
    yield s3_client, ddb_client, sfn_client


def test_upload_document(stub_clients):
    s3_client, _, _ = stub_clients
    body = {
        "tenantId": "tenant",
        "content": base64.b64encode(b"hello").decode(),
        "extension": "txt",
    }
    event = {"httpMethod": "POST", "path": "/uploadJD", "body": json.dumps(body)}
    response = api_app.lambda_handler(event, None)
    assert response["statusCode"] == 200
    s3_client.put_object.assert_called_once()


def test_start_tailor(stub_clients):
    _, ddb_client, sfn_client = stub_clients
    body = {
        "tenantId": "tenant",
        "jobDescriptionKey": "tenant/jd.docx",
        "baseResumeKey": "tenant/resume.docx",
    }
    event = {"httpMethod": "POST", "path": "/tailor", "body": json.dumps(body)}
    response = api_app.lambda_handler(event, None)
    assert response["statusCode"] == 200
    sfn_client.start_execution.assert_called_once()
    ddb_client.put_item.assert_called()
