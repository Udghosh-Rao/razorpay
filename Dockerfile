# Fraud-Spike Sentinel — Production Dockerfile
# Designed for Hugging Face Spaces (Docker SDK)
FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Hugging Face Spaces uses port 7860
ENV PORT=7860

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (includes pre-built model artifacts in models/)
COPY . .

EXPOSE 7860

# Start server — no database init required
CMD ["sh", "-c", "python3 -m uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-7860}"]
