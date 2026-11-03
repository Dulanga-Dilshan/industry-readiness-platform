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

RUN rm -f src/grpc_server/utils/session.py \
    && sed -i '/utils\.session/d' src/grpc_server/__main__.py


FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# The code uses absolute imports (scraper, classifier, grpc_server.proto...),
# so /app/src must be on the import path.
WORKDIR /app/src
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GRPC_HOST=0.0.0.0 \
    GRPC_PORT=50051 \
    ENABLE_REST=1 \
    REST_HOST=0.0.0.0 \
    REST_PORT=8000

USER app
EXPOSE 50051 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,socket; socket.create_connection(('127.0.0.1', int(os.getenv('GRPC_PORT','50051'))), 3)" || exit 1

CMD ["python", "-m", "grpc_server"]