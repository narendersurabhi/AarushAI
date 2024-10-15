FROM python:3.8-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY src/ .
COPY config/ ./config/

# Run the application
CMD ["python", "train_model.py"]
