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
# README.md porque o hatchling valida o build do projeto a partir dele.
# Dois COPYs separados: com múltiplas fontes o Docker copia o CONTEÚDO de
# src/ para o destino (layout flat /app/content_factory), e o editable
# install do pyproject (packages = ["src/content_factory"]) ficava apontando
# para /app/src inexistente — `cf` e `python -c` fora de /app quebravam
# (só funcionava porque o arq injeta o cwd no sys.path; achado pós-deploy
# do worker único, 2026-10-14).
COPY --chown=appuser:appuser src /app/src
COPY --chown=appuser:appuser README.md /app/README.md
# Protocolos de consulta web (`segments/web-queries/<slug>.q1.yaml`) são lidos
# em runtime pelo FileWebQueries; o worker NÃO tem bind-mount, então `segments/`
# precisa estar na imagem. Sem este COPY o coletor cai no legado de 1 consulta
# (em silêncio) e a F1 não roda como aprovada — regressão coberta por
# tests/test_worker_image.py e scripts/smoke_worker_segments.sh.
COPY --chown=appuser:appuser segments /app/segments
USER appuser
RUN uv sync --frozen --all-groups  # instala o projeto; sync pós-start é no-op
