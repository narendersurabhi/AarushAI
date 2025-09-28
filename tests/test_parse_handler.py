import types

import pytest

from src.lambdas.parse_handler import app as parse_app


class DummyClient:
    def __init__(self, service_name):
        self.service_name = service_name

    def analyze_document(self, **kwargs):
        return {"Blocks": []}

    def detect_document_text(self, **kwargs):
        return {"Blocks": []}

    def get_object(self, **kwargs):
        return {"Body": types.SimpleNamespace(read=lambda: b"sample text")}

    def detect_pii_entities(self, **kwargs):
        return {"Entities": []}


@pytest.fixture(autouse=True)
def stub_boto3(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET_NAME", "test-bucket")

    def _client(service_name, *args, **kwargs):
        return DummyClient(service_name)

    monkeypatch.setattr(parse_app.boto3, "client", _client)
    yield


def test_parse_handler_basic():
    event = {
        "tenantId": "tenant-1",
        "jobId": "job-1",
        "jobDescription": {"text": "Data Scientist\nResponsibilities\nBuild models"},
        "baseResume": {"text": "Summary\nExperience\nML Engineer at Org - Jan 2020 - Present\n- Built pipelines"},
        "validatedResumes": [
            {"text": "Summary\nExperience\nData Scientist at Org - Jan 2018 - Jan 2020\n- Led A/B tests"}
        ],
    }

    result = parse_app.lambda_handler(event, None)
    assert "parsed" in result
    assert result["parsed"]["jobDescription"]["responsibilities"]
    assert result["parsed"]["baseResume"]["experience"]
    assert result["parsed"]["validatedResumes"]
    assert result["parsed"]["extractedSkills"]


def test_parse_handler_with_style_profile():
    event = {
        "tenantId": "tenant-2",
        "jobId": "job-2",
        "jobDescription": {"text": "JD text"},
        "baseResume": {"text": "Summary"},
        "validatedResumes": [],
        "styleGuide": {
            "text": "SUMMARY\nSKILLS\n• Python\n• AWS\nEXPERIENCE\nEngineer at Org\n- Built systems",
            "metadata": {
                "fontFamily": "Arial",
                "fontSize": 11,
                "sectionOrder": ["summary", "skills", "experience"],
                "bulletStyle": "dash",
            },
        },
    }

    result = parse_app.lambda_handler(event, None)
    style = result["parsed"].get("styleGuide", {}).get("profile")
    assert style
    assert style["fontFamily"] == "Arial"
    assert style["fontSize"] == 22
    assert style["sectionOrder"][0] == "summary"
    assert style["bulletStyle"] in {"dash", "bullet"}
