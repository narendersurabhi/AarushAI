"""Tests for the evaluation harness quality report."""

from ops.evaluation_harness.evaluate import EvaluationResult, evaluate


def test_evaluate_surfaces_missing_targets_and_keywords() -> None:
    job = {
        "requirements": ["Design ML systems", "Write Python code"],
        "responsibilities": ["Collaborate with stakeholders"],
        "skills": ["Python", "AWS"],
        "competencies": [
            {
                "name": "Leadership",
                "priority": 3,
                "evidenceIndicators": ["cross functional alignment"],
            }
        ],
    }
    resume = {
        "summary": "Demonstrated leadership in cross functional alignment for ML programs.",
        "experience": [
            {
                "title": "ML Engineer",
                "company": "Example",
                "startDate": "2020-01-01",
                "endDate": "2022-12-31",
                "achievements": [
                    "Designing ML systems for patient analytics.",
                    "Led cross functional alignment across product and data teams.",
                    "Automated data pipelines with Python and SQL.",
                ],
            }
        ],
        "skills": ["Python", "SQL"],
    }
    retrieval = {"chunks": [{"text": "Cross functional alignment notes from stakeholders."}]}

    result = evaluate(job, resume, retrieval)

    assert isinstance(result, EvaluationResult)
    assert result.jd_coverage == 0.571
    assert result.ats_keyword_score == 0.667
    assert "Write Python code" in result.missing_jd_targets
    assert "Collaborate with stakeholders" in result.missing_jd_targets
    assert "AWS" in result.missing_keywords

    report = result.to_dict()
    assert "missingCoverageTargets" in report
    assert "missingAtsKeywords" in report
    assert report["missingCoverageTargets"] == result.missing_jd_targets
    assert report["missingAtsKeywords"] == result.missing_keywords
