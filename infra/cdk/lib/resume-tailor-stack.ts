import * as path from 'path';
import { Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as opensearch from 'aws-cdk-lib/aws-opensearchserverless';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';

export class ResumeTailorStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    const dataKey = new kms.Key(this, 'ResumeTailorKmsKey', {
      enableKeyRotation: true,
      alias: 'alias/resume-tailor-platform',
      description: 'KMS key for resume tailoring platform data encryption',
    });

    const uploadBucket = new s3.Bucket(this, 'UploadBucket', {
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: dataKey,
      enforceSSL: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: true,
      removalPolicy: RemovalPolicy.RETAIN,
      serverAccessLogsBucket: new s3.Bucket(this, 'AccessLogsBucket', {
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        encryption: s3.BucketEncryption.S3_MANAGED,
        enforceSSL: true,
        removalPolicy: RemovalPolicy.RETAIN,
      }),
    });

    const artifactBucket = new s3.Bucket(this, 'ArtifactBucket', {
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: dataKey,
      enforceSSL: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const jobsTable = new dynamodb.Table(this, 'JobsTable', {
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      partitionKey: { name: 'tenantJobId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'entityType', type: dynamodb.AttributeType.STRING },
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: dataKey,
      pointInTimeRecovery: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const feedbackTable = new dynamodb.Table(this, 'FeedbackTable', {
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      partitionKey: { name: 'tenantId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'feedbackId', type: dynamodb.AttributeType.STRING },
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: dataKey,
      pointInTimeRecovery: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const vectorCollection = new opensearch.CfnCollection(this, 'ResumeVectorCollection', {
      name: 'resume-tailor-vectors',
      type: 'VECTORSEARCH',
    });

    new opensearch.CfnSecurityPolicy(this, 'ResumeVectorNetworkPolicy', {
      name: 'resume-tailor-network-policy',
      type: 'network',
      policy: JSON.stringify([
        {
          Description: 'Allow VPC access via private endpoints',
          Rules: [
            {
              ResourceType: 'collection',
              Resource: [`collection/${vectorCollection.name}`],
            },
            {
              ResourceType: 'dashboard',
              Resource: [`collection/${vectorCollection.name}`],
            },
          ],
          AllowFromPublic: false,
        },
      ]),
    });

    new opensearch.CfnSecurityPolicy(this, 'ResumeVectorEncryptionPolicy', {
      name: 'resume-tailor-encryption-policy',
      type: 'encryption',
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: 'collection',
              Resource: [`collection/${vectorCollection.name}`],
            },
          ],
          AWSOwnedKey: false,
          KmsArn: dataKey.keyArn,
        },
      ]),
    });

    const sharedDlq = new sqs.Queue(this, 'LambdaDlq', {
      encryption: sqs.QueueEncryption.KMS,
      encryptionMasterKey: dataKey,
      enforceSSL: true,
      retentionPeriod: Duration.days(14),
      visibilityTimeout: Duration.minutes(5),
    });

    const lambdaLogRetention = logs.RetentionDays.ONE_MONTH;

    const lambdaEnv = {
      UPLOAD_BUCKET_NAME: uploadBucket.bucketName,
      ARTIFACT_BUCKET_NAME: artifactBucket.bucketName,
      JOB_TABLE_NAME: jobsTable.tableName,
      FEEDBACK_TABLE_NAME: feedbackTable.tableName,
      VECTOR_COLLECTION_NAME: vectorCollection.name ?? 'resume-tailor-vectors',
      VECTOR_INDEX_NAME: 'resume-tailor-index',
      BEDROCK_GUARDRAIL_ARN: 'arn:aws:bedrock:region:account:guardrail/resume-tailor',
      DEFAULT_TENANT_KEY: 'tenantId',
      ARTIFACT_TTL_DAYS: '7',
    };

    const createLambda = (id: string, handler: string, folder: string): lambda.Function => {
      const fn = new lambda.Function(this, id, {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler,
        code: lambda.Code.fromAsset(path.join(__dirname, '..', '..', 'src', 'lambdas', folder)),
        architecture: lambda.Architecture.ARM_64,
        memorySize: 1024,
        timeout: Duration.minutes(5),
        logRetention: lambdaLogRetention,
        environment: lambdaEnv,
        deadLetterQueueEnabled: true,
        deadLetterQueue: sharedDlq,
      });
      uploadBucket.grantReadWrite(fn);
      artifactBucket.grantReadWrite(fn);
      jobsTable.grantReadWriteData(fn);
      feedbackTable.grantReadWriteData(fn);
      dataKey.grantEncryptDecrypt(fn);
      return fn;
    };

    const parseLambda = createLambda('ParseLambda', 'app.lambda_handler', 'parse_handler');
    parseLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['textract:StartDocumentAnalysis', 'textract:GetDocumentAnalysis', 'textract:StartDocumentTextDetection', 'textract:GetDocumentTextDetection'],
      resources: ['*'],
    }));
    parseLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['comprehend:DetectPiiEntities'],
      resources: ['*'],
    }));

    const embedLambda = createLambda('EmbedLambda', 'app.lambda_handler', 'embed_handler');
    embedLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['*'],
    }));
    embedLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['aoss:CreateCollectionItems', 'aoss:UpdateCollectionItems', 'aoss:BatchGetCollection', 'aoss:APIAccessAll'],
      resources: ['*'],
    }));

    const retrieveLambda = createLambda('RetrieveLambda', 'app.lambda_handler', 'retrieve_handler');
    retrieveLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['aoss:APIAccessAll', 'bedrock:InvokeModel'],
      resources: ['*'],
    }));

    const generateLambda = createLambda('GenerateLambda', 'app.lambda_handler', 'generate_handler');
    generateLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:ApplyGuardrail'],
      resources: ['*'],
    }));

    const validateLambda = createLambda('ValidateLambda', 'app.lambda_handler', 'validate_handler');

    const renderLambda = createLambda('RenderLambda', 'app.lambda_handler', 'render_handler');

    const apiLambda = createLambda('ApiLambda', 'app.lambda_handler', 'api_handlers');
    apiLambda.addEnvironment('STATE_MACHINE_ARN', 'STATE_MACHINE_ARN_PLACEHOLDER');

    const definition = sfn.DefinitionBody.fromFile(path.join(__dirname, '..', '..', 'src', 'stepfunctions', 'main.asl.json'));

    const stateMachine = new sfn.StateMachine(this, 'TailorStateMachine', {
      definitionBody: definition,
      definitionSubstitutions: {
        ParseFunctionArn: parseLambda.functionArn,
        EmbedFunctionArn: embedLambda.functionArn,
        RetrieveFunctionArn: retrieveLambda.functionArn,
        GenerateFunctionArn: generateLambda.functionArn,
        ValidateFunctionArn: validateLambda.functionArn,
        RenderFunctionArn: renderLambda.functionArn,
      },
      timeout: Duration.minutes(15),
      tracingEnabled: true,
      logs: {
        destination: new logs.LogGroup(this, 'StateMachineLogs', {
          retention: logs.RetentionDays.ONE_MONTH,
          removalPolicy: RemovalPolicy.DESTROY,
        }),
        level: sfn.LogLevel.ALL,
      },
    });

    stateMachine.grantStartExecution(apiLambda);

    apiLambda.addEnvironment('STATE_MACHINE_ARN', stateMachine.stateMachineArn);

    const api = new apigw.RestApi(this, 'ResumeTailorApi', {
      restApiName: 'Resume Tailor Service',
      deployOptions: {
        stageName: 'prod',
        loggingLevel: apigw.MethodLoggingLevel.INFO,
        metricsEnabled: true,
        dataTraceEnabled: false,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: apigw.Cors.ALL_METHODS,
      },
    });

    const jdResource = api.root.addResource('uploadJD');
    jdResource.addMethod('POST', new apigw.LambdaIntegration(apiLambda));

    const resumeResource = api.root.addResource('uploadResume');
    resumeResource.addMethod('POST', new apigw.LambdaIntegration(apiLambda));

    const tailorResource = api.root.addResource('tailor');
    tailorResource.addMethod('POST', new apigw.LambdaIntegration(apiLambda));

    const statusResource = api.root.addResource('status').addResource('{jobId}');
    statusResource.addMethod('GET', new apigw.LambdaIntegration(apiLambda));

    const downloadResource = api.root.addResource('download').addResource('{jobId}');
    downloadResource.addMethod('GET', new apigw.LambdaIntegration(apiLambda));

    const listResource = api.root.addResource('artifacts');
    listResource.addMethod('GET', new apigw.LambdaIntegration(apiLambda));

    const rule = new events.Rule(this, 'StuckExecutionRule', {
      schedule: events.Schedule.rate(Duration.hours(1)),
      description: 'Detect and alert on stuck executions and stale artifacts',
    });
    rule.addTarget(new targets.LambdaFunction(apiLambda, {
      event: events.RuleTargetInput.fromObject({ action: 'housekeeping' }),
    }));

    new bedrock.CfnGuardrail(this, 'ResumeTailorGuardrail', {
      name: 'resume-tailor-guardrail',
      blockedInputMessaging: 'The provided content violates acceptable use.',
      blockedOutputsMessaging: 'The generated content was blocked by policy.',
      contentPolicyConfig: {
        filtersConfig: [
          { type: 'SEXUAL', inputStrength: 'HIGH', outputStrength: 'HIGH' },
          { type: 'VIOLENCE', inputStrength: 'HIGH', outputStrength: 'HIGH' },
          { type: 'INSULTS', inputStrength: 'HIGH', outputStrength: 'HIGH' },
        ],
      },
      sensitiveInformationPolicyConfig: {
        piiEntitiesConfig: [
          { type: 'NAME', action: 'ANONYMIZE' },
          { type: 'EMAIL', action: 'ANONYMIZE' },
          { type: 'PHONE', action: 'ANONYMIZE' },
        ],
      },
      topicPolicyConfig: {
        topicsConfig: [
          {
            type: 'DENY',
            name: 'ProhibitedClaims',
            definition: 'Claims that cannot be validated against provided resume content',
          },
        ],
      },
    });
  }
}
