FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["/bin/sh", "-c", "/app/.venv/bin/alembic upgrade head && /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"]
