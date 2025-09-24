"""Render handler - builds DOCX/PDF artifacts and stores them in S3."""
from __future__ import annotations

import json
import os
import time
import zipfile
from io import BytesIO
from typing import Dict, List

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

    s3 = boto3.client("s3")
    renderer = DocumentRenderer(s3)
    job_writer = JobStatusWriter(boto3.client("dynamodb"))
    artifacts = renderer.render_all(
        tenant_id=event["tenantId"],
        job_id=event["jobId"],
        resume=resume,
        change_log=change_log,
        cover_letter=cover_letter,
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

    def render_all(self, tenant_id: str, job_id: str, resume: Dict, change_log: List[Dict], cover_letter: Dict | None) -> Dict:
        timestamp = int(time.time())
        base_prefix = f"{tenant_id}/{job_id}/{timestamp}"

        docx_bytes = self._build_docx(resume, change_log)
        docx_key = f"{base_prefix}/tailored_resume.docx"
        self._put_object(docx_key, docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        pdf_bytes = self._build_pdf(resume)
        pdf_key = f"{base_prefix}/tailored_resume.pdf"
        self._put_object(pdf_key, pdf_bytes, "application/pdf")

        change_log_key = f"{base_prefix}/change_log.json"
        self._put_object(change_log_key, json.dumps(change_log).encode("utf-8"), "application/json")

        cover_letter_key = None
        if cover_letter:
            cover_letter_key = f"{base_prefix}/cover_letter.json"
            self._put_object(cover_letter_key, json.dumps(cover_letter).encode("utf-8"), "application/json")

        return {
            "docxKey": docx_key,
            "pdfKey": pdf_key,
            "changeLogKey": change_log_key,
            "coverLetterKey": cover_letter_key,
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

    def _build_docx(self, resume: Dict, change_log: List[Dict]) -> bytes:
        document_xml = self._build_document_xml(resume, change_log)
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
            archive.writestr("_rels/.rels", _RELS_XML)
            archive.writestr("word/_rels/document.xml.rels", _DOC_RELS_XML)
            archive.writestr("word/document.xml", document_xml)
        return buffer.getvalue()

    def _build_document_xml(self, resume: Dict, change_log: List[Dict]) -> str:
        paragraphs = []
        header_text = f"Tailored Resume for {resume.get('meta', {}).get('role', 'Candidate')}"
        paragraphs.append(_paragraph(header_text))
        paragraphs.append(_paragraph(resume.get("summary", "")))
        paragraphs.append(_paragraph("Experience"))
        for exp in resume.get("experience", []):
            header = f"{exp.get('title', '')} - {exp.get('company', '')} ({exp.get('startDate', '')} - {exp.get('endDate', '')})"
            paragraphs.append(_paragraph(header))
            for bullet in exp.get("achievements", []):
                paragraphs.append(_bullet_paragraph(bullet))
        paragraphs.append(_paragraph("Skills"))
        paragraphs.append(_paragraph(", ".join(resume.get("skills", []))))
        if resume.get("projects"):
            paragraphs.append(_paragraph("Projects"))
            for project in resume.get("projects", []):
                paragraphs.append(_paragraph(f"{project.get('name', '')}: {project.get('description', '')}"))
        if change_log:
            paragraphs.append(_paragraph("Change Log"))
            for item in change_log:
                paragraphs.append(_bullet_paragraph(f"{item.get('type', 'update')}: {item.get('detail', '')}"))
        return _wrap_document(paragraphs)

    def _build_pdf(self, resume: Dict) -> bytes:
        text_lines = [
            f"Tailored Resume for {resume.get('meta', {}).get('role', 'Candidate')}",
            resume.get("summary", ""),
            "Experience:",
        ]
        for exp in resume.get("experience", []):
            text_lines.append(f"- {exp.get('title', '')} at {exp.get('company', '')}")
            for bullet in exp.get("achievements", []):
                text_lines.append(f"  * {bullet}")
        text_lines.append("Skills: " + ", ".join(resume.get("skills", [])))
        text = "\n".join(text_lines)
        return _simple_pdf(text)


def _paragraph(text: str) -> str:
    escaped = _escape_xml(text)
    return f"<w:p><w:r><w:t>{escaped}</w:t></w:r></w:p>"


def _bullet_paragraph(text: str) -> str:
    escaped = _escape_xml(text)
    return (
        "<w:p><w:pPr><w:numPr><w:numId w:val=\"1\"/></w:numPr></w:pPr>"
        f"<w:r><w:t>{escaped}</w:t></w:r></w:p>"
    )


def _wrap_document(paragraphs: List[str]) -> str:
    joined = "".join(paragraphs)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"<w:body>{joined}</w:body></w:document>"
    )


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
