provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "model_storage" {
  bucket = "aarushai-models"
}

resource "aws_ecs_cluster" "model_pipeline" {
  name = "model-pipeline-cluster"
}

resource "aws_ecs_task_definition" "model_pipeline_task" {
  family = "model-pipeline-task"
  network_mode = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  container_definitions = jsonencode([
    {
      name      = "model_pipeline"
      image     = "your_docker_image"
      essential = true
      memory    = 1024
      cpu       = 512
      portMappings = [
        {
          containerPort = 8080
          hostPort      = 8080
        }
      ]
    }
  ])
}

resource "aws_ecs_service" "model_pipeline_service" {
  name            = "model-pipeline-service"
  cluster         = aws_ecs_cluster.model_pipeline.id
  task_definition = aws_ecs_task_definition.model_pipeline_task.arn
  desired_count   = 2
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = ["your_subnet_id"]
    security_groups  = ["your_security_group_id"]
    assign_public_ip = true
  }
}
