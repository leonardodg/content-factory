FROM python:3.14-slim AS base
ENV PYTHONUNBUFFERED=1 UV_INSTALL_DIR=/usr/local/bin
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM base AS dev
RUN uv sync --frozen --all-groups --no-install-project
RUN useradd -m appuser && chown -R appuser /app
USER appuser

FROM dev AS worker
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN uv pip install --system playwright && playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright
# worker NÃO tem bind-mount: o código precisa estar na imagem (target worker);
# README.md porque o hatchling valida o build do projeto a partir dele
COPY --chown=appuser:appuser src ./src README.md ./
USER appuser
RUN uv sync --frozen --all-groups  # instala o projeto; sync pós-start é no-op
