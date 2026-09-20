FROM ghcr.io/astral-sh/uv:0.11.26@sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5 AS uv

FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/migrations ./migrations
COPY apps/api/src ./src
COPY apps/api/docker-entrypoint.py /usr/local/bin/exam-guru-container-entrypoint.py
RUN uv sync --frozen --no-dev --no-editable \
    && groupadd --system --gid 10001 exam-guru \
    && useradd --system --uid 10001 --gid exam-guru --home-dir /app exam-guru \
    && install -d -m 0700 -o exam-guru -g exam-guru /data \
    && chown -R exam-guru:exam-guru /app

ENTRYPOINT ["python", "/usr/local/bin/exam-guru-container-entrypoint.py"]

EXPOSE 8000

CMD ["uvicorn", "exam_guru_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
