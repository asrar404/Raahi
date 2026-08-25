# RAAHI Safety Watcher
#
# A background stream consumer. The HTTP port exists only for health and
# introspection, but it is what the orchestrator probes to decide whether the
# consume loop is still alive.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY app/ app/

RUN useradd --create-home --uid 10003 raahi \
    && chown -R raahi:raahi /app
USER raahi

EXPOSE 8002

# /health/ready reports not_ready when the watcher task has died, so a crashed
# consume loop gets the container restarted rather than left silently idle.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS http://localhost:8002/health/ready || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
