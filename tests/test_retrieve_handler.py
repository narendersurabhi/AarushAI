import json
from unittest.mock import MagicMock

import pytest

from src.lambdas.retrieve_handler import app as retrieve_app


@pytest.fixture(autouse=True)
def stub_clients(monkeypatch):
    bedrock_client = MagicMock()
    bedrock_client.invoke_model.return_value = {"body": json.dumps({"embedding": [0.5, 0.1, 0.2]})}
    opensearch_client = MagicMock()
    opensearch_client.search.return_value = {
        "hits": {
            "hits": [
                {"_score": 0.9, "_source": {"text": "Built ML pipeline", "metadata": {"type": "validated"}}}
            ]
        }
    }
    dynamo_client = MagicMock()
    dynamo_client.query.return_value = {"Items": []}

    def fake_client(name, *args, **kwargs):
        if name == "bedrock-runtime":
            return bedrock_client
        if name == "opensearchserverless":
            return opensearch_client
        if name == "dynamodb":
            return dynamo_client
        raise AssertionError(f"unexpected client {name}")

    monkeypatch.setattr(retrieve_app.boto3, "client", fake_client)
    yield bedrock_client, opensearch_client, dynamo_client


def test_retrieve_handler_returns_context(stub_clients):
    event = {
        "tenantId": "tenant",
        "jobId": "job",
        "parsed": {
            "jobDescription": {"summary": "Lead ML", "requirements": ["Python"], "responsibilities": ["Deploy models"]},
            "baseResume": {
                "experience": [
                    {"title": "Engineer", "achievements": ["Deployed models", "Automated MLOps"]}
                ]
            },
        },
        "embedding": {"vectorIds": ["id-1"]},
    }

    result = retrieve_app.lambda_handler(event, None)
    assert "retrieval" in result
    assert result["retrieval"]["chunks"]
    assert "coverage" in result["retrieval"]
