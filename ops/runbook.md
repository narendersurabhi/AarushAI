# Resume Tailoring Platform Runbook

## Deployment Steps
1. **Bootstrap environment**
   ```bash
   npm install -g aws-cdk
   cd infra/cdk
   npm install
   cdk bootstrap aws://<account>/<region>
   ```
2. **Configure environment variables** (per environment):
   - `BEDROCK_MODEL_ID`
   - `BEDROCK_GUARDRAIL_ARN`
   - `ENABLE_PII_REDACTION` ("true" or "false")
   - `BANNED_CLAIMS` (JSON array)
   - `PROMPT_TEMPLATES_PATH` (optional override)
3. **Deploy infrastructure**
   ```bash
   cdk deploy ResumeTailorStack \
     --parameters UploadBucketRetention=retain \
     --require-approval never
   ```
4. **Seed DynamoDB metadata** (optional)
   - Populate tenant configuration records.
   - Load validated resume metadata via API `/uploadResume`.
5. **Publish prompt templates** to S3 if overriding defaults.
6. **Run canary test** using `/tailor` endpoint with sample documents.

## IAM & Security Notes
- All Lambda roles apply least-privilege managed policies plus inline statements for specific AWS service actions (Textract, Bedrock, OpenSearch, DynamoDB).
- KMS key policy allows Lambda, Step Functions, and S3 to encrypt/decrypt using an encryption context containing `tenantId`.
- API Gateway uses a Cognito authorizer with JWT claims including `tenantId`. The Lambda extracts this claim for tenancy enforcement.
- VPC endpoints should be configured for Bedrock, Textract, DynamoDB, and S3 access. Update Lambda networking configuration after provisioning endpoints.

## Monitoring & Alerting
- CloudWatch dashboards track Step Functions executions, Lambda duration/errors, and API Gateway metrics.
- Set CloudWatch Alarms:
  - `TailorStateMachineFailed` (alarm on >0 failed executions in 5 minutes).
  - `LambdaErrorAlarm` per critical function (Parse, Generate, Render).
  - `API4xxSpike` using API Gateway `4XXError` metric.
- Dead-letter queue `LambdaDlq` receives failed async invocations. Subscribe an SNS topic for triage notifications.

## Operational Tasks
- **Housekeeping**: EventBridge rule triggers API housekeeping every hour to prune expired artifacts from S3.
- **Feedback ingestion**: Analysts can write relevance feedback records into DynamoDB using partition key `tenantId` and sort key `jobId#<timestamp>`.
- **Prompt updates**: Update `docs/prompts/prompt_templates.json` and redeploy or push to S3. Bump configuration version in Parameter Store if used.

## Rollback Procedure
1. **Configuration rollback**: Revert Lambda environment variables to previous known-good values and redeploy via CDK.
2. **Code rollback**: Use CodePipeline to promote the previous successful artifact or redeploy a tagged Git release.
3. **Infrastructure rollback**: Execute `cdk deploy` with `--previous-parameters` referencing the last CloudFormation change set, or delete the stack with `cdk destroy` if redeploying from scratch (data buckets are retained).
4. **Emergency stop**: Disable Step Functions state machine via `aws stepfunctions update-state-machine --state-machine-arn <arn> --status DISABLED` and revoke API access by updating Cognito app client.

## Disaster Recovery
- S3 buckets are versioned with cross-region replication (configure target bucket per environment).
- DynamoDB tables use point-in-time recovery. Restore using `aws dynamodb restore-table-to-point-in-time`.
- Store prompt templates and schema definitions in CodeCommit to enable quick redeployments.

## Support Contacts
- **Primary**: MLOps On-call (`mlops-oncall@example.com`)
- **Secondary**: Platform Engineering (`platform@example.com`)
- **Escalation**: Director, Cloud Platform (`director@example.com`)
