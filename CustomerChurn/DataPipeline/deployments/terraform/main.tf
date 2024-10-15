provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "data_pipeline_bucket" {
  bucket = "your-bucket-name"
}

output "bucket_name" {
  value = aws_s3_bucket.data_pipeline_bucket.bucket
}
