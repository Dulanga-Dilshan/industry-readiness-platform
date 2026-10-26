# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir "uv==0.11.7"
WORKDIR /app

ARG WITH_REST=false

COPY pyproject.toml uv.lock* ./
RUN EXTRA=""; [ "$WITH_REST" = "true" ] && EXTRA="--extra rest"; \
    uv sync --no-dev --no-install-project $EXTRA

# Then the project itself, installed as a normal (non-editable) package
COPY src ./src
RUN EXTRA=""; [ "$WITH_REST" = "true" ] && EXTRA="--extra rest"; \
    uv sync --no-dev --no-editable $EXTRA

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GRPC_PORT=50051 \
    ENABLE_REST=0
USER app
EXPOSE 50051

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,socket; socket.create_connection(('127.0.0.1', int(os.getenv('GRPC_PORT','50051'))), 3)" || exit 1

CMD ["python", "-m", "grpc_server"]