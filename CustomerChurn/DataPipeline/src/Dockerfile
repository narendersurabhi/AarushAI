FROM openjdk:8-jdk-slim

# Install dependencies
RUN apt-get update && apt-get install -y python3-pip && \
    pip3 install pyspark boto3

WORKDIR /app

# Copy application files
COPY src/ .

# Run the application
CMD ["python3", "main_spark.py"]
