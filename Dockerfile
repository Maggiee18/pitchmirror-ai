# Multi-stage build: frontend -> static files served by the FastAPI backend.
FROM node:20-slim AS web
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
# LibreOffice gives pixel-accurate PPTX rendering; without it PPTX slides get a clean text preview.
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-impress && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt matplotlib
COPY backend/ backend/
COPY scripts/ scripts/
COPY samples/ samples/
# regenerate the sample decks if the host stripped binary files (e.g. Hugging Face Spaces)
RUN [ -f samples/crowdsense_viva_demo.pdf ] || python scripts/make_sample_deck.py
COPY --from=web /app/frontend/dist frontend/dist
RUN useradd -m -u 1000 app && chown -R app /app
USER app
WORKDIR /app/backend
EXPOSE 7860
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips='*'"]
