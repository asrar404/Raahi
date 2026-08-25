# RAAHI API Gateway
#
# Runs as a non-root user: this container terminates traffic from the public
# internet, so a container escape should not land on uid 0.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is needed for the healthcheck below; build-essential is dropped again
# in the same layer so it does not bloat the image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Requirements first: this layer is cached unless the pinned versions change,
# so ordinary code edits skip the dependency install entirely.
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY app/ app/

RUN useradd --create-home --uid 10001 raahi \
    && chown -R raahi:raahi /app
USER raahi

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
