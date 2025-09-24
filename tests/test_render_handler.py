import json
from unittest.mock import MagicMock

import pytest

from src.lambdas.render_handler import app as render_app


@pytest.fixture(autouse=True)
def stub_clients(monkeypatch):
    monkeypatch.setenv("ARTIFACT_BUCKET_NAME", "artifact-bucket")
    monkeypatch.setenv("JOB_TABLE_NAME", "job-table")

    render_app.ARTIFACT_BUCKET = "artifact-bucket"
    render_app.JOB_TABLE_NAME = "job-table"

    s3_client = MagicMock()
    dynamo_client = MagicMock()

    def fake_client(name, *args, **kwargs):
        if name == "s3":
            return s3_client
        if name == "dynamodb":
            return dynamo_client
        raise AssertionError(f"unexpected client {name}")

    monkeypatch.setattr(render_app.boto3, "client", fake_client)
    yield s3_client, dynamo_client


def test_render_handler_uploads_artifacts(stub_clients):
    s3_client, dynamo_client = stub_clients
    event = {
        "tenantId": "tenant",
        "jobId": "job",
        "generation": {
            "tailoredResume": {
                "meta": {"role": "Engineer"},
                "summary": "Summary text",
                "experience": [
                    {
                        "title": "Engineer",
                        "company": "X",
                        "startDate": "2020",
                        "endDate": "Present",
                        "achievements": ["Improved throughput by 20%"],
                    }
                ],
                "skills": ["Python"],
                "projects": [],
            },
            "changeLog": [{"type": "bullet", "detail": "Updated metric", "rationale": "Quantified impact"}],
        },
    }

    result = render_app.lambda_handler(event, None)
    assert "artifacts" in result
    assert s3_client.put_object.call_count >= 3
    dynamo_client.put_item.assert_called_once()
