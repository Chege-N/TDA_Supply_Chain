FROM python:3.11-slim

LABEL maintainer="Chege-N"
LABEL description="Real-time topological anomaly detection for global supply chains"

# System dependencies
RUN apt-get update && apt-get install -y \
    gcc g++ cmake \
    libboost-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Create non-root user (security best practice)
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

# Port 7860 = Hugging Face Spaces default (free production)
ENV PORT=7860
EXPOSE 7860

# Healthcheck on the correct port
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:7860/health || exit 1

CMD ["python", "main.py", "api", "--host", "0.0.0.0", "--port", "7860"]
