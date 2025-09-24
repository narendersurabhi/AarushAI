"""Parse handler - orchestrates Textract parsing and normalization."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError

UPLOAD_BUCKET = os.getenv("UPLOAD_BUCKET_NAME", "")
JOB_TABLE_NAME = os.getenv("JOB_TABLE_NAME", "")
ENABLE_PII_ENV = os.getenv("ENABLE_PII_REDACTION", "false").lower() == "true"


def lambda_handler(event: Dict, _context) -> Dict:
    """Lambda entry point.

    Parameters
    ----------
    event: Dict
        Event payload containing tenant/job identifiers and document pointers.
    _context:
        Lambda context object (unused).

    Returns
    -------
    Dict
        Event payload augmented with parsed document structures.
    """

    validator = EventValidator()
    validator.validate_root(event)

    textract = TextractParser(
        textract_client=boto3.client("textract"),
        s3_client=boto3.client("s3"),
    )
    pii_redactor: Optional[PIIRedactor] = None
    if ENABLE_PII_ENV or event.get("options", {}).get("enablePiiRedaction"):
        pii_redactor = PIIRedactor(boto3.client("comprehend"))

    job_description = textract.parse_job_description(event["jobDescription"])
    base_resume = textract.parse_resume(event["baseResume"])
    validated_resumes = [textract.parse_resume(doc) for doc in event.get("validatedResumes", [])]

    if pii_redactor:
        job_description = pii_redactor.redact_job(job_description)
        base_resume = pii_redactor.redact_resume(base_resume)
        validated_resumes = [pii_redactor.redact_resume(resume) for resume in validated_resumes]

    parsed_payload = {
        **event,
        "parsed": {
            "jobDescription": job_description,
            "baseResume": base_resume,
            "validatedResumes": validated_resumes,
            "extractedSkills": SkillMiner.aggregate_skills(job_description, base_resume, validated_resumes),
        },
        "timestamp": time.time(),
    }

    return parsed_payload


@dataclass
class DocumentPointer:
    s3Key: Optional[str] = None
    text: Optional[str] = None
    metadata: Optional[Dict] = None
    documentType: Optional[str] = None

    @staticmethod
    def from_event(event_fragment: Dict) -> "DocumentPointer":
        return DocumentPointer(
            s3Key=event_fragment.get("s3Key"),
            text=event_fragment.get("text"),
            metadata=event_fragment.get("metadata", {}),
            documentType=event_fragment.get("documentType"),
        )


class EventValidator:
    required_fields = {"tenantId", "jobId", "jobDescription", "baseResume"}

    def validate_root(self, event: Dict) -> None:
        missing = [field for field in self.required_fields if field not in event]
        if missing:
            raise ValueError(f"Missing required fields in event: {missing}")
        if not isinstance(event.get("validatedResumes", []), list):
            raise ValueError("validatedResumes must be a list if provided")


class TextractParser:
    """Wrapper to normalize Textract output into canonical schemas."""

    def __init__(self, textract_client, s3_client):
        self._textract = textract_client
        self._s3 = s3_client

    def parse_job_description(self, fragment: Dict) -> Dict:
        pointer = DocumentPointer.from_event(fragment)
        text = self._fetch_text(pointer)
        return normalize_job_description(text, pointer.metadata or {})

    def parse_resume(self, fragment: Dict) -> Dict:
        pointer = DocumentPointer.from_event(fragment)
        text = self._fetch_text(pointer)
        return normalize_resume(text, pointer.metadata or {})

    def _fetch_text(self, pointer: DocumentPointer) -> str:
        if pointer.text:
            return pointer.text
        if not pointer.s3Key:
            raise ValueError("Document pointer must include either text or s3Key")

        document_bytes = self._download_object(pointer.s3Key)
        return self._run_textract(document_bytes, pointer.documentType)

    def _download_object(self, key: str) -> bytes:
        try:
            response = self._s3.get_object(Bucket=UPLOAD_BUCKET, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise RuntimeError(f"Unable to download object {key}: {exc}") from exc
        return response["Body"].read()

    def _run_textract(self, document_bytes: bytes, document_type: Optional[str]) -> str:
        """Run Textract and return the concatenated text."""
        if document_type and document_type.lower() == "plain-text":
            return document_bytes.decode("utf-8")

        try:
            response = self._textract.analyze_document(
                Document={"Bytes": document_bytes},
                FeatureTypes=["TABLES", "FORMS"],
            )
        except (ClientError, BotoCoreError):
            response = self._textract.detect_document_text(Document={"Bytes": document_bytes})

        lines: List[str] = []
        for block in response.get("Blocks", []):
            if block.get("BlockType") in {"LINE", "CELL"} and block.get("Text"):
                lines.append(block["Text"])
        return "\n".join(lines)


class PIIRedactor:
    def __init__(self, comprehend_client):
        self._client = comprehend_client

    def redact_job(self, job: Dict) -> Dict:
        job = json.loads(json.dumps(job))
        job["responsibilities"] = [self._redact_text(text) for text in job.get("responsibilities", [])]
        job["requirements"] = [self._redact_text(text) for text in job.get("requirements", [])]
        job["summary"] = self._redact_text(job.get("summary", ""))
        return job

    def redact_resume(self, resume: Dict) -> Dict:
        resume = json.loads(json.dumps(resume))
        resume["summary"] = self._redact_text(resume.get("summary", ""))
        resume["experience"] = [
            {
                **role,
                "achievements": [self._redact_text(text) for text in role.get("achievements", [])],
            }
            for role in resume.get("experience", [])
        ]
        resume["projects"] = [
            {**project, "description": self._redact_text(project.get("description", ""))}
            for project in resume.get("projects", [])
        ]
        return resume

    def _redact_text(self, text: str) -> str:
        if not text:
            return text
        response = self._client.detect_pii_entities(Text=text, LanguageCode="en")
        spans = sorted(response.get("Entities", []), key=lambda item: item["BeginOffset"])
        redacted = []
        last_index = 0
        for entity in spans:
            begin, end = entity["BeginOffset"], entity["EndOffset"]
            redacted.append(text[last_index:begin])
            redacted.append("[REDACTED]")
            last_index = end
        redacted.append(text[last_index:])
        return "".join(redacted)


class SkillMiner:
    @staticmethod
    def aggregate_skills(job: Dict, base_resume: Dict, validated_resumes: List[Dict]) -> List[Dict]:
        skill_counts: Dict[str, Dict[str, object]] = {}
        source_documents = [
            ("job", job.get("skills", [])),
            ("base", base_resume.get("skills", [])),
        ] + [(res.get("meta", {}).get("sourceKey", f"validated-{idx}"), res.get("skills", [])) for idx, res in enumerate(validated_resumes)]

        for source, skills in source_documents:
            for skill in skills:
                normalized = skill.lower().strip()
                if not normalized:
                    continue
                record = skill_counts.setdefault(
                    normalized,
                    {"skill": normalized, "sources": set(), "frequency": 0},
                )
                record["sources"].add(source)
                record["frequency"] = int(record["frequency"]) + 1
        enriched = [
            {"skill": record["skill"], "sources": sorted(list(record["sources"])), "frequency": record["frequency"]}
            for record in skill_counts.values()
        ]
        enriched.sort(key=lambda item: (-int(item["frequency"]), item["skill"]))
        return enriched


def normalize_job_description(raw_text: str, metadata: Dict) -> Dict:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    title = metadata.get("role") or _extract_with_regex(lines, r"(?i)^role[:\-]\s*(.*)")
    summary_lines = []
    requirements: List[str] = []
    responsibilities: List[str] = []
    skills: List[str] = []
    section = "summary"
    for line in lines:
        lower = line.lower()
        if any(keyword in lower for keyword in ["responsibilit", "what you'll do"]):
            section = "responsibilities"
            continue
        if any(keyword in lower for keyword in ["requirement", "qualifications", "what you'll bring"]):
            section = "requirements"
            continue
        if any(keyword in lower for keyword in ["skill", "technolog", "tool"]):
            section = "skills"
            continue
        if section == "summary":
            summary_lines.append(line)
        elif section == "responsibilities":
            responsibilities.append(line)
        elif section == "requirements":
            requirements.append(line)
        else:
            skills.append(line)

    return {
        "meta": {
            "title": title or metadata.get("title", "Unknown Role"),
            "industry": metadata.get("industry", "Unknown"),
            "location": metadata.get("location", "Unknown"),
            "source": metadata.get("source", "upload"),
        },
        "summary": " ".join(summary_lines)[:2000],
        "responsibilities": responsibilities,
        "requirements": requirements,
        "skills": skills or _derive_skills_from_text(lines),
        "rawText": raw_text,
    }


def normalize_resume(raw_text: str, metadata: Dict) -> Dict:
    sections = _split_sections(raw_text)
    summary = " ".join(sections.get("summary", [])).strip()
    experience = _parse_experience(sections.get("experience", []))
    education = sections.get("education", [])
    skills = sections.get("skills", _derive_skills_from_text(raw_text.splitlines()))
    projects = _parse_projects(sections.get("projects", []))

    return {
        "meta": {
            "sourceKey": metadata.get("sourceKey"),
            "role": metadata.get("role"),
            "industry": metadata.get("industry"),
            "outcome": metadata.get("outcome"),
            "updatedAt": metadata.get("updatedAt", time.strftime("%Y-%m-%d")),
        },
        "summary": summary,
        "experience": experience,
        "education": education,
        "skills": skills,
        "projects": projects,
        "rawText": raw_text,
    }


def _split_sections(raw_text: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {"summary": [], "experience": [], "education": [], "skills": [], "projects": []}
    current = "summary"
    for line in raw_text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        lower = clean.lower()
        if re.match(r"^(professional )?summary", lower):
            current = "summary"
            continue
        if lower.startswith("experience") or "experience" in lower:
            current = "experience"
            continue
        if lower.startswith("education"):
            current = "education"
            continue
        if lower.startswith("skill"):
            current = "skills"
            continue
        if lower.startswith("project"):
            current = "projects"
            continue
        sections.setdefault(current, []).append(clean)
    return sections


def _parse_experience(lines: List[str]) -> List[Dict]:
    experience: List[Dict] = []
    current_role: Dict[str, object] = {}
    bullet_pattern = re.compile(r"^[\u2022\-\*]\s*(.*)")
    date_pattern = re.compile(r"(\w+\s+\d{4})\s*[\u2013\-]\s*(Present|\w+\s+\d{4})", re.IGNORECASE)
    for line in lines:
        bullet = bullet_pattern.match(line)
        if bullet:
            if current_role:
                current_role.setdefault("achievements", []).append(bullet.group(1))
            continue
        date_match = date_pattern.search(line)
        if date_match:
            if current_role:
                experience.append(current_role)
            current_role = {
                "title": line.split(" at ")[0].strip(),
                "company": line.split(" at ")[-1].strip(),
                "startDate": date_match.group(1),
                "endDate": date_match.group(2),
                "achievements": [],
            }
        else:
            current_role.setdefault("achievements", []).append(line)
    if current_role:
        experience.append(current_role)
    return experience


def _parse_projects(lines: List[str]) -> List[Dict]:
    projects: List[Dict] = []
    current: Dict[str, str] = {}
    for line in lines:
        if ":" in line and not current:
            name, description = line.split(":", 1)
            current = {"name": name.strip(), "description": description.strip()}
            projects.append(current)
        else:
            current.setdefault("description", "")
            current["description"] = f"{current['description']} {line.strip()}".strip()
    return projects


def _derive_skills_from_text(lines: List[str]) -> List[str]:
    keywords = set()
    word_pattern = re.compile(r"[A-Za-z+#]{2,}")
    for line in lines:
        for word in word_pattern.findall(line):
            if word.isupper() or word[0].isupper():
                keywords.add(word.strip(",."))
    return sorted(keywords)


def _extract_with_regex(lines: List[str], pattern: str) -> Optional[str]:
    regex = re.compile(pattern)
    for line in lines:
        match = regex.search(line)
        if match:
            return match.group(1)
    return None
