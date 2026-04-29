# Stage 1: Build the frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
# Copy package files first to leverage Docker cache
COPY frontend/package*.json ./
RUN npm install
# Copy the rest of the frontend code
COPY frontend/ ./
# Build the production assets
RUN npm run build

# Stage 2: Build the backend and combine
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and its dependencies for scraping
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libxshmfence1 libnss3 libxfixes3 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright
RUN mkdir -p $PLAYWRIGHT_BROWSERS_PATH \
    && playwright install chromium

# Copy application code
COPY vision-service /app/vision-service
COPY scraping-service /app/scraping-service
COPY ml-service /app/ml-service
COPY genai-service /app/genai_service
COPY api_gateway /app/api_gateway

# Copy the built frontend from Stage 1
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Create a non-root user for security
RUN useradd -m appuser \
    && chown -R appuser:appuser /app \
    && chown -R appuser:appuser $PLAYWRIGHT_BROWSERS_PATH

USER appuser

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "api_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
