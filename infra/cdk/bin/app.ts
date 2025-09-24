#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { ResumeTailorStack } from '../lib/resume-tailor-stack';

const app = new cdk.App();

new ResumeTailorStack(app, 'ResumeTailorStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
});
