# Evaluation Harness & Human Review Recipe

## Automated Checks
Run the evaluator after each tailoring job to quantify quality metrics:

```bash
python ops/evaluation_harness/evaluate.py \
  --jd artifacts/parsed_jd.json \
  --resume artifacts/tailored_resume.json \
  --retrieval artifacts/retrieval_context.json
```

The script reports JD coverage, ATS keyword coverage, hallucination flags, bullet consistency, and reading-grade level. Integrate the JSON output into CodeBuild or Step Functions to gate production releases.

### Output Report Fields

Each run prints a single JSON object with the following fields:

| Field | Description | Remediation Guidance |
| ----- | ----------- | -------------------- |
| `jdCoverage` | Ratio of JD targets (requirements, responsibilities, skills, competency cues) that appear in the tailored resume. | Rewrite bullets or summary lines so that every high-priority JD statement is reflected. |
| `missingCoverageTargets` | Ordered list of JD targets that were not detected in the resume text after stemming/token normalization. | Provide explicit achievements or phrasing that mention these targets. |
| `atsKeywordScore` | Fraction of ATS keywords satisfied. Keywords include JD skills, explicit keyword lists, and competency evidence indicators. | Add missing keywords to the skills block or weave them into experience bullets. |
| `missingAtsKeywords` | Keywords absent from the resume tokens. | Add to the resume skills list or weave into relevant bullets. |
| `hallucinations` | Bullets that lack supporting evidence from the JD or retrieval context. | Replace the bullet with factual content or enrich the retrieval set. |
| `consistency` | Measures variance in bullet lengths (1.0 is perfectly consistent). | Trim overly long bullets and expand short ones for balance. |
| `readabilityGradeLevel` | Flesch–Kincaid reading grade level for the summary and experience sections. | Target grade ≤10 for broad readability unless the tenant requires technical depth. |

```json
{
  "jdCoverage": 0.75,
  "missingCoverageTargets": [
    "Build data governance dashboards",
    "Define stakeholder communication cadences"
  ],
  "atsKeywordScore": 0.6,
  "missingAtsKeywords": ["Snowflake", "Airflow"],
  "hallucinations": [],
  "consistency": 0.83,
  "readabilityGradeLevel": 9.4
}
```

> **Tip:** Feed the `missingCoverageTargets` and `missingAtsKeywords` arrays back into a secondary prompt (“gap fill” stage) to automatically iterate on weak drafts.

## Human-in-the-loop Review Screen
Host a lightweight review UI using Amazon S3 static website hosting or AWS Amplify:

1. **Build frontend** (single-page HTML/JS) with the following panels:
   - **JD Summary** – render parsed competencies and requirements.
   - **Tailored Resume Diff** – highlight rewritten bullets using the delta schema.
   - **Change Log & Rationales** – display entries returned by the pipeline.
   - **Approval Controls** – buttons for `approve`, `request-edits`, `reject`, with optional comment box.
2. **Deploy**
   - Upload static assets to `s3://<tenant-review-site>` with static website hosting enabled, or connect the repository to AWS Amplify Hosting for CI/CD.
   - Configure Cognito + Amplify to require reviewer login (groups: `reviewer`, `admin`).
3. **Integrate Feedback**
   - On approval/reject, POST to an API Gateway endpoint writing feedback into the DynamoDB feedback table (`tenantId`, `feedbackId`, `text`, `score`).
   - Use the stored feedback to improve retrieval weighting and prompts over time.

## Review Checklist
- Metrics within thresholds: JD coverage ≥0.7, ATS keyword score ≥0.6, no hallucinations.
- Resume length ≤2 pages (~1,000 words) unless override flag present.
- Tone consistent with tenant style guide stored in Parameter Store.
