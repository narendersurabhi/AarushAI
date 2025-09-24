import json
from unittest.mock import MagicMock

import pytest

from src.lambdas.embed_handler import app as embed_app


@pytest.fixture(autouse=True)
def stub_clients(monkeypatch):
    monkeypatch.setenv("VECTOR_COLLECTION_NAME", "collection")
    monkeypatch.setenv("VECTOR_INDEX_NAME", "index")

    bedrock_client = MagicMock()
    bedrock_client.invoke_model.return_value = {"body": json.dumps({"embedding": [0.1, 0.2, 0.3]})}
    opensearch_client = MagicMock()
    opensearch_client.batch_put_document.return_value = {}

    def fake_client(name, *args, **kwargs):
        if name == "bedrock-runtime":
            return bedrock_client
        if name == "opensearchserverless":
            return opensearch_client
        raise AssertionError(f"unexpected client {name}")

    monkeypatch.setattr(embed_app.boto3, "client", fake_client)
    yield bedrock_client, opensearch_client


def test_embed_handler_creates_vectors(stub_clients):
    event = {
        "tenantId": "tenant",
        "jobId": "job",
        "parsed": {
            "jobDescription": {"summary": "Analyze data", "requirements": ["SQL"], "responsibilities": ["Modeling"]},
            "baseResume": {"experience": [{"title": "Analyst", "achievements": ["Improved pipeline"]}]},
            "validatedResumes": [],
            "extractedSkills": [{"skill": "Python", "sources": ["job"], "frequency": 2}],
        },
    }

    result = embed_app.lambda_handler(event, None)
    assert "embedding" in result
    assert result["embedding"]["documentCount"] >= 1
