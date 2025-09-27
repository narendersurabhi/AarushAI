"""Validate handler - enforces safety, factuality, and schema compliance."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List

BANNED_CLAIMS = set(json.loads(os.getenv("BANNED_CLAIMS", "[]")))
REQUIRED_SECTIONS = ["summary", "experience", "skills"]


def lambda_handler(event: Dict, _context) -> Dict:
    if "generation" not in event:
        raise ValueError("Generation results are required prior to validation")

    generation = event["generation"]
    resume = generation.get("tailoredResume", {})
    change_log = generation.get("changeLog", [])
    cover_letter = generation.get("coverLetter")

    validator = ResumeValidator()
    issues = validator.validate_resume(resume)
    issues.extend(validator.validate_change_log(change_log))
    if cover_letter:
        issues.extend(validator.validate_cover_letter(cover_letter))

    if BANNED_CLAIMS:
        issues.extend(validator.detect_banned_claims(resume, BANNED_CLAIMS))

    status = "PASSED" if not issues else "FAILED"
    metrics = validator.compute_metrics(resume, event.get("retrieval", {}))

    return {
        **event,
        "validation": {
            "status": status,
            "issues": issues,
            "metrics": metrics,
            "timestamp": time.time(),
        },
    }


@dataclass
class ValidationIssue:
    code: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message}


class ResumeValidator:
    email_regex = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    phone_regex = re.compile(r"\b(?:\+?\d{1,3})?[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{4}\b")

    def validate_resume(self, resume: Dict) -> List[Dict]:
        issues: List[ValidationIssue] = []
        if not isinstance(resume, dict) or not resume:
            issues.append(ValidationIssue(code="EMPTY_RESUME", message="Tailored resume payload missing or invalid"))
            return [issue.to_dict() for issue in issues]

        for section in REQUIRED_SECTIONS:
            if section not in resume or not resume.get(section):
                issues.append(ValidationIssue(code="MISSING_SECTION", message=f"Required section '{section}' is missing"))

        experience = resume.get("experience", [])
        for idx, role in enumerate(experience):
            if "achievements" not in role or not role.get("achievements"):
                issues.append(ValidationIssue(code="EMPTY_ACHIEVEMENTS", message=f"Experience item {idx} missing achievements"))
            for bullet in role.get("achievements", []):
                if len(bullet) > 500:
                    issues.append(ValidationIssue(code="BULLET_TOO_LONG", message=f"Achievement exceeds 500 characters: {bullet[:50]}"))
                if not re.search(r"\d", bullet):
                    issues.append(ValidationIssue(code="NO_METRIC", message=f"Achievement lacks metric: {bullet[:50]}"))
        if not resume.get("skills"):
            issues.append(ValidationIssue(code="NO_SKILLS", message="Skills section is empty"))
        else:
            duplicates = {skill.lower() for skill in resume.get("skills", []) if resume.get("skills", []).count(skill) > 1}
            if duplicates:
                issues.append(ValidationIssue(code="DUPLICATE_SKILL", message=f"Duplicate skills detected: {sorted(list(duplicates))}"))

        textual_content = json.dumps(resume)
        if self.email_regex.search(textual_content):
            issues.append(ValidationIssue(code="PII_EMAIL", message="Email detected in tailored resume"))
        if self.phone_regex.search(textual_content):
            issues.append(ValidationIssue(code="PII_PHONE", message="Phone number detected in tailored resume"))

        return [issue.to_dict() for issue in issues]

    def validate_change_log(self, change_log: List[Dict]) -> List[Dict]:
        issues: List[ValidationIssue] = []
        for idx, entry in enumerate(change_log):
            if "type" not in entry or "detail" not in entry:
                issues.append(ValidationIssue(code="CHANGE_LOG_FORMAT", message=f"Change log entry {idx} missing type/detail"))
            if entry.get("rationale") is None:
                issues.append(ValidationIssue(code="CHANGE_LOG_RATIONALE", message=f"Change log entry {idx} missing rationale"))
        return [issue.to_dict() for issue in issues]

    def validate_cover_letter(self, cover_letter: Dict) -> List[Dict]:
        issues: List[ValidationIssue] = []
        if len(cover_letter.get("body", "")) > 4000:
            issues.append(ValidationIssue(code="COVER_LENGTH", message="Cover letter exceeds 4000 characters"))
        tone = cover_letter.get("tone")
        if tone and tone not in {"professional", "enthusiastic", "formal", "friendly"}:
            issues.append(ValidationIssue(code="COVER_TONE", message=f"Unsupported tone '{tone}'"))
        return [issue.to_dict() for issue in issues]

    def detect_banned_claims(self, resume: Dict, banned: set) -> List[Dict]:
        issues: List[ValidationIssue] = []
        text = json.dumps(resume).lower()
        for keyword in banned:
            if keyword.lower() in text:
                issues.append(ValidationIssue(code="BANNED_CLAIM", message=f"Banned keyword present: {keyword}"))
        return [issue.to_dict() for issue in issues]

    def compute_metrics(self, resume: Dict, retrieval: Dict) -> Dict:
        experience = resume.get("experience", [])
        bullet_count = sum(len(item.get("achievements", [])) for item in experience)
        coverage = retrieval.get("coverage", {})
        return {
            "bulletCount": bullet_count,
            "skillsCount": len(resume.get("skills", [])),
            "jdCoverage": coverage,
        }
