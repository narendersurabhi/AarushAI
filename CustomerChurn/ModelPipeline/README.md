
# ModelPipeline

## Description

The `ModelPipeline` module is responsible for training and evaluating machine learning models. It supports running multiple ML algorithms in parallel on different instances, saving the trained models to an S3 bucket.

## Project Structure

```plaintext
ModelPipeline/
├── src/
│   ├── config/
│   │   ├── algorithms.json
│   ├── train_model.py
│   ├── evaluate_model.py
│   ├── save_model.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── dependencies/
├── models/
│   ├── model.pkl
│   └── metrics/
│       ├── evaluation_report.txt
├── deployments/
│   ├── terraform/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── k8s_deployment.yml
│   ├── k8s_service.yml
│   └── docker-compose.yml
└── README.md
