# AWS Resume Tailoring Platform Architecture

```
                             +----------------------------+
                             |        Amazon Cognito      |
                             +-------------+--------------+
                                           |
                                           v
                              +------------+-------------+
                              | Amazon API Gateway (REST)|
                              +------------+-------------+
                                           |
                                           v
                              +------------+-------------+
                              |    AWS Lambda (API)      |
                              +------------+-------------+
                                           |
                                           v
             +-----------------------------+-------------------------------+
             |                             |                               |
             v                             v                               v
    +--------+---------+        +---------+---------+             +--------+--------+
    |  S3 Upload Buckets|        | DynamoDB Metadata |             | EventBridge     |
    |  (tenant prefixes)|        |  (jobs, feedback) |             | Schedules/Rules |
    +--------+---------+        +---------+---------+             +--------+--------+
             |                             |                               |
             v                             v                               v
     +-------+-----------------------------+-------------------------------+
     |                         Step Functions (Orchestrator)               |
     |    Ingest -> Parse -> Embed -> Retrieve -> Generate -> Validate ->  |
     |                      Render -> Persist -> Notify                    |
     +-------+-----------------------------+-------------------------------+
             |                             |                               |
             v                             v                               v
 +-----------+---------+     +-------------+-------------+    +------------+-------------+
 |  Lambda: Parse      |     | Lambda: Retrieve/Generate |    | Lambda: Render Artifacts |
 |  (Textract, Comprehend)|  |   (Bedrock, OpenSearch)   |    | (DOCX/PDF, Change Log)    |
 +-----------+---------+     +-------------+-------------+    +------------+-------------+
             |                             |                               |
             v                             v                               v
     +-------+---------+         +--------+----------+           +--------+-----------+
     | Amazon Textract  |         | Amazon OpenSearch |           | Amazon S3 Artifacts|
     | (PDF/DOCX parse) |         | Serverless (Vectors)|         |  (Tailored resumes) |
     +------------------+         +--------------------+         +---------------------+
                                          |
                                          v
                                 +--------+---------+
                                 | Amazon Bedrock   |
                                 | (Titan Embed &   |
                                 |  Claude/Llama)   |
                                 +------------------+

```

## Data Flow Overview
1. **Upload** – Tenants authenticate via Cognito and upload job descriptions and resumes through API Gateway -> Lambda. Files land in S3 using tenant-specific prefixes.
2. **Orchestration** – Lambda enqueues a Step Functions execution keyed by `jobId`. EventBridge can trigger retries or scheduled cleanups.
3. **Parsing** – Parse Lambda invokes Textract (and Comprehend if enabled) to convert documents to normalized JSON aligned to the canonical resume schema.
4. **Embedding & Retrieval** – Embed Lambda generates Titan embeddings and stores vectors in Amazon OpenSearch Serverless. Retrieve Lambda assembles a RAG context using JD, validated resumes, and feedback stored in DynamoDB.
5. **Generation & Validation** – Generate Lambda runs Bedrock foundation models via guarded prompts. Validate Lambda enforces policy checks, schema compliance, and rejection criteria.
6. **Rendering** – Render Lambda produces DOCX and PDF outputs plus change logs, applying an optional sample resume style guide (section order, headings, fonts) before persisting artifacts in S3 for signed URL retrieval via API.
7. **Observability & Governance** – CloudWatch captures logs/metrics, SQS DLQs capture failures, and CloudTrail + Bedrock guardrails enforce compliance.

## Multi-Tenancy Notes
- All S3 keys, DynamoDB partition keys, and OpenSearch index documents include a `tenantId` attribute to enforce isolation.
- IAM policies apply tenant context through API Gateway authorizers and Lambda environment-derived session claims.
- KMS keys enforce per-tenant encryption context via `kms:EncryptionContext` conditions.
