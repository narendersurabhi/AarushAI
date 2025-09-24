# Cost Estimate

Assumptions:
- Region: us-east-1
- Average resume package: 3 pages (~1,500 tokens) + one JD (~1,000 tokens)
- 30-day month
- Foundation model pricing based on public Amazon Bedrock rates (subject to change).

| Tier | Tailoring Jobs / Month | Concurrent Burst | Notes |
| ---- | ---------------------- | ---------------- | ----- |
| Low  | 2,000                  | 10               | Startup teams, limited automation |
| Medium | 20,000               | 50               | Mid-size HR tech platform |
| High | 100,000                | 100              | Enterprise talent platform |

## Monthly Cost Breakdown (USD)

| Service | Unit Cost | Low | Medium | High | Optimization Notes |
| ------- | --------- | --- | ------ | ---- | ------------------ |
| S3 Storage & Requests | $0.023/GB-mo, $0.005 per 1K PUT | $12 | $45 | $180 | Lifecycle rules to Glacier after 30 days |
| AWS Lambda (API & Processing) | $0.00001667 per GB-s | $45 | $320 | $1,400 | Use ARM64, tune memory to 1024 MB |
| Textract | $1.50 per 1K pages | $9 | $90 | $450 | Cache parsed text for re-runs |
| Comprehend (optional) | $1.00 per 1K units | $4 | $40 | $200 | Enable only for regulated tenants |
| Bedrock Titan Embeddings | $0.00013 per 1K tokens | $8 | $80 | $400 | Batch embed validated resumes |
| Bedrock Text Generation (Claude) | $0.003 per 1K output tokens | $60 | $600 | $3,000 | Adjust temperature, reuse prompts |
| OpenSearch Serverless | $0.144 per OCUs-hour | $210 | $420 | $720 | Scale down collection when idle |
| DynamoDB (On-demand) | $1.25 per million R/W units | $15 | $120 | $480 | Consider adaptive capacity + TTL |
| Step Functions | $0.025 per 1K transitions | $5 | $40 | $200 | Combine states where possible |
| CloudWatch (Logs + Metrics) | $0.50 per GB ingest | $12 | $60 | $250 | Apply log retention filters |
| CodePipeline/Build | $1 per active pipeline + build minutes | $30 | $60 | $120 | Use GitHub Actions for dev branches |
| Misc (Data transfer, KMS) | $10 | $50 | $200 | Request service quotas to avoid throttling |
| **Total** |  | **$420** | **$1,925** | **$7,600** |  |

## Cost Controls
- Implement per-tenant throttles with API Gateway usage plans.
- Leverage Step Functions callback patterns to avoid idle waits.
- Use Savings Plans for Lambda and Bedrock once baseline is known.
- Enable CloudWatch anomaly detection to spot runaway jobs.
