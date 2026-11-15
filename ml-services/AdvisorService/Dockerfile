# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=0
RUN pip install --no-cache-dir uv
WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY src ./src


FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /data && chown app:app /data

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY knowledge_base /app/knowledge_base
COPY curriculum /app/curriculum
COPY data /app/data
COPY contracts /app/contracts
COPY mock_backend /app/mock_backend

# Absolute imports (ml_common, advisor, ...): /app/src must be on the import path.
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src:/app" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GRPC_HOST=0.0.0.0 \
    GRPC_PORT=50052 \
    ENABLE_REST=1 \
    REST_HOST=0.0.0.0 \
    REST_PORT=8001 \
    DATA_DIR=/data \
    KNOWLEDGE_DIR=/app/knowledge_base \
    CURRICULUM_PATH=/app/curriculum/curriculum.yaml \
    GLOSSARY_PATH=/app/data/skills_glossary.txt

USER app
EXPOSE 50052 8001
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,socket; socket.create_connection(('127.0.0.1', int(os.getenv('GRPC_PORT','50052'))), 3)" || exit 1

CMD ["python", "-m", "advisor"]
