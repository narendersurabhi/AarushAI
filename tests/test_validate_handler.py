import json

from src.lambdas.validate_handler import app as validate_app


def test_validate_handler_pass(monkeypatch):
    monkeypatch.setenv("BANNED_CLAIMS", json.dumps([]))
    event = {
        "generation": {
            "tailoredResume": {
                "summary": "Engineer",
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
            },
            "changeLog": [{"type": "bullet", "detail": "Updated metric", "rationale": "Quantified impact"}],
        },
        "retrieval": {"coverage": {"requirements": 0.8}},
    }

    result = validate_app.lambda_handler(event, None)
    assert result["validation"]["status"] == "PASSED"
    assert not result["validation"]["issues"]


def test_validate_handler_banned(monkeypatch):
    monkeypatch.setenv("BANNED_CLAIMS", json.dumps(["wizard"]))
    validate_app.BANNED_CLAIMS = {"wizard"}
    event = {
        "generation": {
            "tailoredResume": {
                "summary": "Wizard",
                "experience": [
                    {
                        "title": "Wizard",
                        "company": "Magic",
                        "startDate": "2020",
                        "endDate": "Present",
                        "achievements": ["Cast spell 100"],
                    }
                ],
                "skills": ["Wizardry"],
            },
            "changeLog": [{"type": "bullet", "detail": "Updated", "rationale": ""}],
        },
    }

    result = validate_app.lambda_handler(event, None)
    assert result["validation"]["status"] == "FAILED"
    assert any(issue["code"] == "BANNED_CLAIM" for issue in result["validation"]["issues"])
