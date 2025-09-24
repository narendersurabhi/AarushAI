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
