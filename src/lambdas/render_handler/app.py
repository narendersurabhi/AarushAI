"""Render handler - builds DOCX/PDF artifacts and stores them in S3."""
from __future__ import annotations

import json
import os
import time
import zipfile
from io import BytesIO
from typing import Dict, Iterable, List

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ARTIFACT_BUCKET = os.getenv("ARTIFACT_BUCKET_NAME", "")
JOB_TABLE_NAME = os.getenv("JOB_TABLE_NAME", "")
ARTIFACT_TTL_DAYS = int(os.getenv("ARTIFACT_TTL_DAYS", "7"))


def lambda_handler(event: Dict, _context) -> Dict:
    if "generation" not in event:
        raise ValueError("Generation output required for rendering")

    resume = event["generation"].get("tailoredResume", {})
    change_log = event["generation"].get("changeLog", [])
    cover_letter = event["generation"].get("coverLetter")
    style_profile = (
        event.get("parsed", {}).get("styleGuide", {}).get("profile")
        if isinstance(event.get("parsed", {}).get("styleGuide"), dict)
        else None
    )
    style_source = None
    if isinstance(event.get("parsed", {}).get("styleGuide"), dict):
        style_source = event["parsed"]["styleGuide"].get("source")
    if not style_source and isinstance(event.get("styleGuide"), dict):
        style_source = {
            "s3Key": event.get("styleGuide", {}).get("s3Key"),
            "metadata": event.get("styleGuide", {}).get("metadata", {}),
        }

    s3 = boto3.client("s3")
    renderer = DocumentRenderer(s3)
    job_writer = JobStatusWriter(boto3.client("dynamodb"))
    artifacts = renderer.render_all(
        tenant_id=event["tenantId"],
        job_id=event["jobId"],
        resume=resume,
        change_log=change_log,
        cover_letter=cover_letter,
        style_profile=style_profile,
        style_source=style_source,
    )

    job_writer.write_success(
        tenant_id=event["tenantId"],
        job_id=event["jobId"],
        artifacts=artifacts,
    )

    return {**event, "artifacts": artifacts}


class DocumentRenderer:
    def __init__(self, s3_client):
        self.s3 = s3_client

    def render_all(
        self,
        tenant_id: str,
        job_id: str,
        resume: Dict,
        change_log: List[Dict],
        cover_letter: Dict | None,
        style_profile: Dict | None,
        style_source: Dict | None,
    ) -> Dict:
        timestamp = int(time.time())
        base_prefix = f"{tenant_id}/{job_id}/{timestamp}"

        docx_bytes = self._build_docx(resume, change_log, style_profile)
        docx_key = f"{base_prefix}/tailored_resume.docx"
        self._put_object(docx_key, docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        pdf_bytes = self._build_pdf(resume, change_log, style_profile)
        pdf_key = f"{base_prefix}/tailored_resume.pdf"
        self._put_object(pdf_key, pdf_bytes, "application/pdf")

        change_log_key = f"{base_prefix}/change_log.json"
        self._put_object(change_log_key, json.dumps(change_log).encode("utf-8"), "application/json")

        style_profile_key = None
        if style_profile:
            style_profile_key = f"{base_prefix}/style_profile.json"
            self._put_object(style_profile_key, json.dumps(style_profile).encode("utf-8"), "application/json")

        cover_letter_key = None
        if cover_letter:
            cover_letter_key = f"{base_prefix}/cover_letter.json"
            self._put_object(cover_letter_key, json.dumps(cover_letter).encode("utf-8"), "application/json")

        style_source_key = None
        if style_source and style_source.get("s3Key"):
            style_source_key = style_source["s3Key"]

        return {
            "docxKey": docx_key,
            "pdfKey": pdf_key,
            "changeLogKey": change_log_key,
            "coverLetterKey": cover_letter_key,
            "styleProfileKey": style_profile_key,
            "styleSourceKey": style_source_key,
            "expiresAt": timestamp + ARTIFACT_TTL_DAYS * 86400,
        }

    def _put_object(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self.s3.put_object(
                Bucket=ARTIFACT_BUCKET,
                Key=key,
                Body=data,
                ContentType=content_type,
                ServerSideEncryption="aws:kms",
            )
        except (ClientError, BotoCoreError) as exc:
            raise RuntimeError(f"Failed to upload artifact {key}: {exc}") from exc

    def _build_docx(self, resume: Dict, change_log: List[Dict], style_profile: Dict | None) -> bytes:
        document_xml = self._build_document_xml(resume, change_log, style_profile)
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
            archive.writestr("_rels/.rels", _RELS_XML)
            archive.writestr("word/_rels/document.xml.rels", _DOC_RELS_XML)
            archive.writestr("word/document.xml", document_xml)
        return buffer.getvalue()

    def _build_document_xml(self, resume: Dict, change_log: List[Dict], style_profile: Dict | None) -> str:
        formatter = LayoutFormatter(style_profile)
        paragraphs: List[str] = []
        header_text = formatter.render_header(resume.get("meta", {}).get("role", "Candidate"))
        paragraphs.append(formatter.paragraph(header_text, bold=True, size_delta=4))

        sections = formatter.section_order(["summary", "skills", "experience", "projects", "education"])

        for section in sections:
            if section == "summary" and resume.get("summary"):
                paragraphs.extend(formatter.section_block("Summary", [resume.get("summary", "")], bulleted=False))
            elif section == "skills" and resume.get("skills"):
                skill_lines = [skill for skill in resume.get("skills", [])]
                paragraphs.extend(formatter.section_block("Skills", skill_lines, bulleted=True))
            elif section == "experience" and resume.get("experience"):
                paragraphs.append(formatter.paragraph(formatter.section_heading("Experience"), bold=True))
                for role in resume.get("experience", []):
                    paragraphs.append(formatter.paragraph(formatter.format_experience_header(role), bold=True))
                    for bullet in role.get("achievements", []):
                        paragraphs.append(formatter.bullet(bullet))
            elif section == "projects" and resume.get("projects"):
                project_lines = [
                    f"{project.get('name', '')}: {project.get('description', '')}" for project in resume.get("projects", [])
                    if project.get("name") or project.get("description")
                ]
                paragraphs.extend(formatter.section_block("Projects", project_lines, bulleted=True))
            elif section == "education" and resume.get("education"):
                paragraphs.extend(formatter.section_block("Education", resume.get("education", []), bulleted=True))

        if change_log:
            change_lines = [f"{item.get('type', 'update')}: {item.get('detail', '')}" for item in change_log]
            paragraphs.extend(formatter.section_block("Change Log", change_lines, bulleted=True))

        return _wrap_document(paragraphs)

    def _build_pdf(self, resume: Dict, change_log: List[Dict], style_profile: Dict | None) -> bytes:
        formatter = LayoutFormatter(style_profile)
        lines: List[str] = []
        lines.append(formatter.render_header(resume.get("meta", {}).get("role", "Candidate")))
        sections = formatter.section_order(["summary", "skills", "experience", "projects", "education"])

        for section in sections:
            if section == "summary" and resume.get("summary"):
                lines.append(formatter.section_heading("Summary"))
                lines.append(formatter.body_line(resume.get("summary", "")))
            elif section == "skills" and resume.get("skills"):
                lines.append(formatter.section_heading("Skills"))
                for skill in resume.get("skills", []):
                    lines.append(formatter.bullet_line(skill))
            elif section == "experience" and resume.get("experience"):
                lines.append(formatter.section_heading("Experience"))
                for role in resume.get("experience", []):
                    lines.append(formatter.body_line(formatter.format_experience_header(role)))
                    for bullet in role.get("achievements", []):
                        lines.append(formatter.bullet_line(bullet))
            elif section == "projects" and resume.get("projects"):
                lines.append(formatter.section_heading("Projects"))
                for project in resume.get("projects", []):
                    lines.append(formatter.bullet_line(f"{project.get('name', '')}: {project.get('description', '')}"))
            elif section == "education" and resume.get("education"):
                lines.append(formatter.section_heading("Education"))
                for item in resume.get("education", []):
                    lines.append(formatter.bullet_line(item))

        if change_log:
            lines.append(formatter.section_heading("Change Log"))
            for item in change_log:
                lines.append(formatter.bullet_line(f"{item.get('type', 'update')}: {item.get('detail', '')}"))

        text = "\n".join(lines)
        return _simple_pdf(text)


def _wrap_document(paragraphs: List[str]) -> str:
    joined = "".join(paragraphs)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"<w:body>{joined}</w:body></w:document>"
    )


class LayoutFormatter:
    def __init__(self, style_profile: Dict | None):
        profile = style_profile or {}
        self.font_family = profile.get("fontFamily", "Calibri")
        self.font_size = int(profile.get("fontSize", 22))
        self.heading_case = profile.get("headingCase") or "title"
        self.bullet_style = profile.get("bulletStyle", "bullet")
        self.section_density = profile.get("layoutDensity", "balanced")
        self.section_dividers = profile.get("includeSectionDividers", False)
        self.section_order_pref = profile.get("sectionOrder")

    def section_order(self, available: Iterable[str]) -> List[str]:
        available_list = list(available)
        if isinstance(self.section_order_pref, list):
            ordered = [section for section in self.section_order_pref if section in available_list]
            for section in available_list:
                if section not in ordered:
                    ordered.append(section)
            return ordered
        return available_list

    def render_header(self, role: str) -> str:
        role = role or "Candidate"
        if self.heading_case == "upper":
            return f"TAILORED RESUME – {role.upper()}"
        if self.heading_case == "title":
            return f"Tailored Resume – {role.title()}"
        return f"Tailored Resume – {role}"

    def section_heading(self, section: str) -> str:
        base = section if " " in section else section.replace("_", " ")
        base = base.strip()
        if not base:
            base = section
        if self.heading_case == "upper":
            return base.upper()
        if self.heading_case == "title":
            return base.title()
        return base.capitalize()

    def paragraph(self, text: str, bold: bool = False, size_delta: int = 0) -> str:
        escaped = _escape_xml(text)
        size = max(10, self.font_size + size_delta)
        rpr = (
            f"<w:rPr><w:rFonts w:ascii=\"{self.font_family}\" w:hAnsi=\"{self.font_family}\"/>"
            f"<w:sz w:val=\"{size}\"/><w:szCs w:val=\"{size}\"/>"
        )
        if bold:
            rpr += "<w:b/>"
        rpr += "</w:rPr>"
        spacing = "<w:pPr><w:spacing w:after=\"80\"/></w:pPr>"
        if self.section_density == "condensed":
            spacing = "<w:pPr><w:spacing w:after=\"40\"/></w:pPr>"
        elif self.section_density == "spacious":
            spacing = "<w:pPr><w:spacing w:after=\"120\"/></w:pPr>"
        return f"<w:p>{spacing}<w:r>{rpr}<w:t>{escaped}</w:t></w:r></w:p>"

    def bullet(self, text: str) -> str:
        symbol = {
            "dash": "-",
            "asterisk": "*",
        }.get(self.bullet_style, "•")
        return self.paragraph(f"{symbol} {text}")

    def section_block(self, heading: str, lines: List[str], bulleted: bool = False) -> List[str]:
        cleaned = [line for line in lines if line]
        if not cleaned:
            return []
        block: List[str] = [self.paragraph(self.section_heading(heading), bold=True)]
        if self.section_dividers:
            block.append(self.paragraph("―" * 20))
        for line in cleaned:
            if bulleted:
                block.append(self.bullet(line))
            else:
                block.append(self.paragraph(line))
        return block

    def format_experience_header(self, role: Dict) -> str:
        title = role.get("title", "")
        company = role.get("company", "")
        start = role.get("startDate", "")
        end = role.get("endDate", "")
        company_part = f" – {company}" if company else ""
        date_part = ""
        if start or end:
            dash = "-" if self.heading_case == "upper" else "–"
            start_text = start or ""
            end_text = end or "Present"
            if start_text and end_text:
                date_part = f" ({start_text} {dash} {end_text})"
            else:
                date_part = f" ({start_text or end_text})"
        return f"{title}{company_part}{date_part}".strip()

    def body_line(self, text: str) -> str:
        return text

    def bullet_line(self, text: str) -> str:
        symbol = {
            "dash": "-",
            "asterisk": "*",
        }.get(self.bullet_style, "•")
        return f"  {symbol} {text}"


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _simple_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
    stream_bytes = stream.encode("utf-8")
    pdf = BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = []

    def _write_obj(obj_id: int, content: str) -> None:
        offsets.append(pdf.tell())
        pdf.write(f"{obj_id} 0 obj\n{content}\nendobj\n".encode("utf-8"))

    _write_obj(1, "<< /Type /Catalog /Pages 2 0 R >>")
    _write_obj(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    _write_obj(3, "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >>")
    offsets.append(pdf.tell())
    pdf.write(f"4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("utf-8"))
    pdf.write(stream_bytes)
    pdf.write(b"\nendstream\nendobj\n")
    offsets.append(pdf.tell())
    pdf.write(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    xref_start = pdf.tell()
    pdf.write(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in [0] + offsets:
        pdf.write(f"{offset:010d} 00000 n \n".encode("utf-8"))
    pdf.write(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n")
    pdf.write(str(xref_start).encode("utf-8"))
    pdf.write(b"\n%%EOF")
    return pdf.getvalue()


class JobStatusWriter:
    def __init__(self, dynamodb_client):
        self.client = dynamodb_client

    def write_success(self, tenant_id: str, job_id: str, artifacts: Dict) -> None:
        if not JOB_TABLE_NAME:
            return
        try:
            self.client.put_item(
                TableName=JOB_TABLE_NAME,
                Item={
                    "tenantJobId": {"S": f"{tenant_id}#{job_id}"},
                    "entityType": {"S": "RESULT"},
                    "artifacts": {"S": json.dumps(artifacts)},
                    "status": {"S": "COMPLETED"},
                    "updatedAt": {"N": str(int(time.time()))},
                },
            )
        except (ClientError, BotoCoreError) as exc:
            raise RuntimeError(f"Failed to persist job status: {exc}") from exc


_CONTENT_TYPES_XML = (
    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
    "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
    "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
    "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
    "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
    "</Types>"
)

_RELS_XML = (
    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
    "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
    "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>"
    "</Relationships>"
)

_DOC_RELS_XML = (
    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
    "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"/>"
)
