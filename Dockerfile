# Multi-stage build: frontend -> static files served by the FastAPI backend.
FROM node:20-slim AS web
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# LibreOffice gives pixel-accurate PPTX rendering; remove this line for a ~500MB smaller image (text previews are used instead)
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-impress && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY samples/ samples/
COPY --from=web /app/frontend/dist frontend/dist
RUN useradd -m app && chown -R app /app
USER app
WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
