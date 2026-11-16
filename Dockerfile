# syntax=docker/dockerfile:1.7
FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN --mount=type=secret,id=doxa_build_ca,required=false \
    if [ -f /run/secrets/doxa_build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/doxa_build_ca; fi; \
    npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DOXA_DATA_DIR=/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 libpango-1.0-0 libpangoft2-1.0-0 \
    libharfbuzz-subset0 libjpeg62-turbo libopenjp2-7 fontconfig fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY infra/requirements-runtime.lock ./infra/requirements-runtime.lock
COPY doxa/ ./doxa/
ARG DOXA_OCR=false
RUN --mount=type=secret,id=doxa_build_ca,required=false \
    if [ -f /run/secrets/doxa_build_ca ]; then export PIP_CERT=/run/secrets/doxa_build_ca; fi; \
    pip install --no-cache-dir --upgrade 'pip>=26.2,<27' && \
    if [ "$DOXA_OCR" = "true" ]; then pip install --no-cache-dir '.[ocr]'; \
    else pip install --no-cache-dir --require-hashes -r infra/requirements-runtime.lock && pip install --no-cache-dir --no-deps .; fi
COPY corpus/ ./corpus/
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN groupadd --gid 10001 doxa && useradd --uid 10001 --gid doxa --no-create-home doxa \
    && mkdir -p /data /models && chown -R doxa:doxa /data /models /app
USER doxa
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/api/health',headers={'x-doxa-service-token':os.getenv('DOXA_INTERNAL_TOKEN','')}); urllib.request.urlopen(r, timeout=4)" || exit 1
CMD ["uvicorn", "doxa.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
