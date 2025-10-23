"""Evaluation harness for tailored resumes.

The script inspects pipeline outputs (parsed job description, tailored resume,
and optional retrieval context) and reports a quality summary.  Originally the
tool surfaced only aggregate scores, which made it difficult for reviewers to
pinpoint concrete gaps in the tailored content.  The evaluator now enumerates
missing coverage items and ATS keywords directly in the report so that
operators can act on the findings without re-running analysis by hand.
"""
from __future__ import annotations

import argparse
import json
import math
import string
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set, Tuple


@dataclass
class EvaluationResult:
    """Structured representation of evaluation metrics."""

    jd_coverage: float
    missing_jd_targets: List[str]
    ats_keyword_score: float
    missing_keywords: List[str]
    hallucination_flags: List[str]
    consistency_score: float
    readability_grade: float

    def to_dict(self) -> Dict:
        return {
            "jdCoverage": self.jd_coverage,
            "missingCoverageTargets": self.missing_jd_targets,
            "atsKeywordScore": self.ats_keyword_score,
            "missingAtsKeywords": self.missing_keywords,
            "hallucinations": self.hallucination_flags,
            "consistency": self.consistency_score,
            "readabilityGradeLevel": self.readability_grade,
        }


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Evaluate tailored resume output")
    parser.add_argument("--jd", required=True, help="Path to parsed job description JSON")
    parser.add_argument("--resume", required=True, help="Path to tailored resume JSON")
    parser.add_argument("--retrieval", required=False, help="Path to retrieval context JSON")
    args = parser.parse_args(argv)

    job = _load_json(args.jd)
    resume = _load_json(args.resume)
    retrieval = _load_json(args.retrieval) if args.retrieval else {"chunks": []}

    result = evaluate(job, resume, retrieval)
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def evaluate(job: Dict, resume: Dict, retrieval: Dict) -> EvaluationResult:
    jd_coverage, missing_targets = _coverage_score(job, resume)
    ats_keyword_score, missing_keywords = _ats_keyword_score(job, resume)
    hallucinations = _hallucination_flags(job, resume, retrieval)
    consistency = _consistency_score(resume)
    readability = _readability_grade(resume)
    return EvaluationResult(
        jd_coverage,
        missing_targets,
        ats_keyword_score,
        missing_keywords,
        hallucinations,
        consistency,
        readability,
    )


def _coverage_score(job: Dict, resume: Dict) -> Tuple[float, List[str]]:
    """Calculate JD coverage and list uncovered targets.

    Coverage is derived from requirements, responsibilities, skills, and
    competency cues surfaced in the parsed JD.  A target is considered covered
    when all significant tokens appear in the resume text (after light
    stemming).  Returning the missing targets provides direct guidance on which
    statements the generation step should reinforce.
    """

    targets = list(_iter_jd_targets(job))
    if not targets:
        return 1.0, []

    resume_tokens = _collect_resume_tokens(resume)
    missing: List[str] = []
    hits = 0

    for target in targets:
        tokenized = _tokenize(target)
        if tokenized and tokenized.issubset(resume_tokens):
            hits += 1
        else:
            missing.append(target)

    score = round(hits / len(targets), 3)
    return score, missing


def _ats_keyword_score(job: Dict, resume: Dict) -> Tuple[float, List[str]]:
    """Score ATS keyword coverage and return missing keywords."""

    keywords = _collect_keywords(job)
    if not keywords:
        return 1.0, []

    resume_tokens = _collect_resume_tokens(resume) | _tokenize_iter(resume.get("skills", []))
    missing: List[str] = []
    hits = 0

    for keyword in keywords:
        tokens = _tokenize(keyword)
        if tokens and tokens.issubset(resume_tokens):
            hits += 1
        else:
            missing.append(keyword)

    score = round(hits / len(keywords), 3)
    return score, missing


def _hallucination_flags(job: Dict, resume: Dict, retrieval: Dict) -> List[str]:
    evidence: Set[str] = set()
    for chunk in retrieval.get("chunks", []):
        evidence.add(chunk.get("text", "").lower())
    job_text = json.dumps(job).lower()
    evidence.add(job_text)
    hallucinations: List[str] = []
    for bullet in _iter_bullets(resume):
        normalized = bullet.lower()
        if not any(normalized[:80] in ev for ev in evidence):
            hallucinations.append(bullet)
    return hallucinations


def _consistency_score(resume: Dict) -> float:
    experiences = resume.get("experience", [])
    if not experiences:
        return 0.0
    bullet_lengths = [len(bullet.split()) for bullet in _iter_bullets(resume)]
    if not bullet_lengths:
        return 0.0
    mean_length = sum(bullet_lengths) / len(bullet_lengths)
    variance = sum((length - mean_length) ** 2 for length in bullet_lengths) / len(bullet_lengths)
    stdev = math.sqrt(variance)
    max_allowed = max(mean_length * 0.6, 3)
    inconsistent = [length for length in bullet_lengths if abs(length - mean_length) > max_allowed]
    score = max(0.0, 1.0 - (len(inconsistent) / len(bullet_lengths)))
    return round(score, 3)


def _readability_grade(resume: Dict) -> float:
    text = " ".join([resume.get("summary", "")] + list(_iter_bullets(resume)))
    words = [word for word in text.split() if word]
    if not words:
        return 12.0
    sentences = max(text.count("."), 1)
    syllables = sum(_approx_syllables(word) for word in words)
    flesch_kincaid = 0.39 * (len(words) / sentences) + 11.8 * (syllables / len(words)) - 15.59
    return round(max(flesch_kincaid, 1.0), 2)


def _approx_syllables(word: str) -> int:
    vowels = "aeiouy"
    word = word.lower()
    count = 0
    prev_char_vowel = False
    for char in word:
        is_vowel = char in vowels
        if is_vowel and not prev_char_vowel:
            count += 1
        prev_char_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


def _iter_bullets(resume: Dict) -> List[str]:
    bullets: List[str] = []
    for exp in resume.get("experience", []):
        bullets.extend(exp.get("achievements", []))
    return bullets


def _load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _iter_jd_targets(job: Dict) -> Iterable[str]:
    yield from job.get("requirements", [])
    yield from job.get("responsibilities", [])
    yield from job.get("skills", [])
    for competency in job.get("competencies", []) or []:
        name = competency.get("name")
        if name:
            yield name
        for indicator in competency.get("evidenceIndicators", []) or []:
            if indicator:
                yield indicator


def _collect_resume_tokens(resume: Dict) -> Set[str]:
    """Tokenize all salient resume text for coverage matching."""

    segments: List[str] = [resume.get("summary", "")]
    segments.extend(_iter_bullets(resume))
    segments.extend(resume.get("skills", []))
    for project in resume.get("projects", []) or []:
        segments.append(project.get("name", ""))
        segments.append(project.get("description", ""))
    return _tokenize_iter(segments)


def _tokenize_iter(values: Sequence[str]) -> Set[str]:
    tokens: Set[str] = set()
    for value in values or []:
        tokens |= _tokenize(value)
    return tokens


def _tokenize(value: str) -> Set[str]:
    cleaned = value.lower() if isinstance(value, str) else ""
    if not cleaned:
        return set()
    stripped = cleaned.translate(str.maketrans({ch: " " for ch in string.punctuation}))
    tokens = {_normalize_token(token) for token in stripped.split() if token}
    return {token for token in tokens if token}


def _normalize_token(token: str) -> str:
    token = token.strip()
    if not token:
        return ""

    if token.endswith("ing") and len(token) > 4:
        token = token[:-3]
    elif token.endswith("ed") and len(token) > 3:
        token = token[:-2]
    elif token.endswith("es") and len(token) > 4:
        token = token[:-2]
    elif token.endswith("s") and len(token) > 3:
        token = token[:-1]
    return token


def _collect_keywords(job: Dict) -> List[str]:
    keywords: List[str] = []
    keywords.extend(job.get("skills", []))
    keywords.extend(job.get("keywords", []))
    for competency in job.get("competencies", []) or []:
        keywords.extend(competency.get("evidenceIndicators", []) or [])
    return [kw for kw in keywords if isinstance(kw, str) and kw.strip()]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
