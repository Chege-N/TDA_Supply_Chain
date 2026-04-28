FROM python:3.11-slim

LABEL maintainer="TDA Supply Chain Team"
LABEL description="Real-time topological anomaly detection for global supply chains"

# System dependencies
RUN apt-get update && apt-get install -y \
    gcc g++ cmake \
    libboost-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Create non-root user
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

# Health check (updated to port 7860)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:7860/health || exit 1

# Hugging Face Spaces expects port 7860
EXPOSE 7860

# Update command to use port 7860
CMD ["python", "main.py", "api", "--host", "0.0.0.0", "--port", "7860"]
