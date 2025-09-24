"""Evaluation harness for tailored resumes."""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from typing import Dict, List, Set


@dataclass
class EvaluationResult:
    jd_coverage: float
    ats_keyword_score: float
    hallucination_flags: List[str]
    consistency_score: float
    readability_grade: float

    def to_dict(self) -> Dict:
        return {
            "jdCoverage": self.jd_coverage,
            "atsKeywordScore": self.ats_keyword_score,
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
    jd_coverage = _coverage_score(job, resume)
    ats_keyword_score = _ats_keyword_score(job, resume)
    hallucinations = _hallucination_flags(job, resume, retrieval)
    consistency = _consistency_score(resume)
    readability = _readability_grade(resume)
    return EvaluationResult(jd_coverage, ats_keyword_score, hallucinations, consistency, readability)


def _coverage_score(job: Dict, resume: Dict) -> float:
    targets: List[str] = job.get("requirements", []) + job.get("responsibilities", []) + job.get("skills", [])
    if not targets:
        return 1.0
    total = len(targets)
    hits = 0
    resume_text = json.dumps(resume).lower()
    for item in targets:
        if item and item.lower() in resume_text:
            hits += 1
    return round(hits / total, 3)


def _ats_keyword_score(job: Dict, resume: Dict) -> float:
    keywords = set([skill.lower() for skill in job.get("skills", [])])
    resume_skills = set([skill.lower() for skill in resume.get("skills", [])])
    if not keywords:
        return 1.0
    matches = keywords.intersection(resume_skills)
    return round(len(matches) / len(keywords), 3)


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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
