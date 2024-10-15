# AarushAI
An end to end machine learning project.

Following is the structure of the project

AarushAI/
├── DataPipeline/ (Data Ingestion)
│   ├── src/
│   │   ├── main_spark.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── deployments/
│   │   ├── terraform/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   └── outputs.tf
│   └── README.md
├── ModelPipeline/ (Model Training and Evaluation)
│   ├── src/
│   │   ├── config/
│   │   │   ├── algorithms.json
│   │   ├── train_model.py
│   │   ├── evaluate_model.py
│   │   ├── save_model.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   ├── models/
│   │   ├── model.pkl
│   │   └── metrics/
│   │       ├── evaluation_report.txt
│   ├── deployments/
│   │   ├── terraform/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   └── outputs.tf
│   │   ├── k8s_deployment.yml
│   │   ├── k8s_service.yml
│   │   └── docker-compose.yml
│   └── README.md
├── prediction_microservice/ (Prediction Microservice)
│   ├── src/
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── deployments/
│   │   ├── docker-compose.yml
│   │   ├── k8s_deployment.yml
│   │   └── k8s_service.yml
│   └── README.md
├── monitoring/ (Monitoring)
│   ├── src/
│   │   ├── monitor.py
│   │   └── dependencies/
│   └── README.md
├── shared/
│   ├── utils/
│   ├── datasets/
│   └── scripts/
└── README.md
