# DataPipeline

## Description

The `DataPipeline` module is responsible for data ingestion and preprocessing, storing the preprocessed data in an S3 bucket for further use in the machine learning pipeline.

## Project Structure

```plaintext
DataPipeline/
├── src/
│   ├── main_spark.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── dependencies/
├── deployments/
│   ├── terraform/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
└── README.md
