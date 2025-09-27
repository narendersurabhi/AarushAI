import json

import pytest

from src.lambdas.generate_handler import app as generate_app


@pytest.fixture(autouse=True)
def stub_bedrock(monkeypatch):
    monkeypatch.setenv("PROMPT_TEMPLATES_PATH", "dummy")

    def fake_load(self):
        return {
            "competency_extraction": {"template": "STAGE:competency {jobDescription}"},
            "experience_alignment": {"template": "STAGE:align {competencies}"},
            "bullet_rewrite": {"template": "STAGE:rewrite {alignmentPlan}"},
            "skills_harmonization": {"template": "STAGE:skills {alignmentPlan}"},
            "consistency_check": {"template": "STAGE:consistency {rewrittenBullets}"},
        }

    monkeypatch.setattr(generate_app.PromptLoader, "load", fake_load)

    def fake_invoke(self, prompt: str, stop_sequences=None):  # pylint: disable=unused-argument
        if "competency" in prompt:
            return json.dumps({"competencies": [{"name": "ML Ops", "priority": 1}]})
        if "align" in prompt and "rewrite" not in prompt:
            return json.dumps({"alignments": [{"competencyName": "ML Ops", "sourceExperience": {"title": "Engineer"}}]})
        if "rewrite" in prompt:
            return json.dumps({"bullets": [{"competencyName": "ML Ops", "rewrittenBullet": "Delivered 20% faster"}]})
        if "skills" in prompt:
            return json.dumps({"skills": ["Python"], "taxonomyTags": ["ml"]})
        return json.dumps({
            "tailoredResume": {
                "summary": "ML Engineer",
                "experience": [{"title": "Engineer", "company": "X", "startDate": "2020", "endDate": "Present", "achievements": ["Delivered 20% faster"]}],
                "skills": ["Python"],
            },
            "changeLog": [{"type": "bullet", "detail": "Updated metric", "rationale": "Quantified impact"}]
        })

    monkeypatch.setattr(generate_app.BedrockTextGenerator, "invoke", fake_invoke)
    yield


def test_generate_handler_chain(stub_bedrock):
    event = {
        "tenantId": "tenant",
        "jobId": "job",
        "parsed": {
            "jobDescription": {"summary": "Lead ML", "requirements": [], "responsibilities": []},
            "baseResume": {"experience": [{"title": "Engineer", "company": "X", "startDate": "2020", "endDate": "Present", "achievements": ["Did work"]}]},
            "validatedResumes": [],
            "extractedSkills": [],
        },
        "retrieval": {"chunks": []},
        "options": {"includeCoverLetter": False},
    }

    result = generate_app.lambda_handler(event, None)
    assert "generation" in result
    assert result["generation"]["tailoredResume"]["skills"] == ["Python"]
    assert result["generation"]["changeLog"]
