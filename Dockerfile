FROM python:3.11-slim

LABEL maintainer="Felix Chege N."
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

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

EXPOSE 8001

CMD ["python", "main.py", "api", "--host", "0.0.0.0", "--port", "8001"]
