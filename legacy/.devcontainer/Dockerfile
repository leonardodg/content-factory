FROM python:3.14-slim-bookworm AS build

RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential \
    curl \
    git \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*


# Gravar Files *.pyc no disco: 1 = no
ENV PYTHONDONTWRITEBYTECODE 1

# Não fazer Buffer
ENV PYTHONUNBUFFERED 1

# Outras variáveis de ambiente do Python
ENV PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

# Poetry
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_CACHE_DIR=/var/cache/pypoetry \
    POETRY_HOME=/usr/local \
    POETRY_VERSION=2.2

# Additional Env Vars
ARG DEV=false
ARG VIRTUAL_ENV=/app/.venv

ENV VIRTUAL_ENV=$VIRTUAL_ENV \
    PATH="$VIRTUAL_ENV/bin:$PATH"

# Create venv and install Poetry into it
RUN python -m venv $VIRTUAL_ENV \
    && $VIRTUAL_ENV/bin/pip install --upgrade pip \
    && $VIRTUAL_ENV/bin/pip install poetry==$POETRY_VERSION

WORKDIR /app
# Copy repository into image (build context should be the repo root). Using
# `COPY . /app` is compatible with `docker build -f .devcontainer/Dockerfile .`.
COPY . /app

# Use BuildKit cache mount syntax directly on the RUN instruction so the
# mount is not interpreted by the shell. Install into the created venv by
# calling the poetry binary inside $VIRTUAL_ENV. Use && so the install runs
# after the echo and not the other way around.
RUN --mount=type=cache,target=$POETRY_CACHE_DIR \
    sh -c 'if [ "${DEV}" = "true" ] || [ "${DEV}" = "True" ]; then \
            echo "OK - DEV is true" && $VIRTUAL_ENV/bin/poetry install --with dev --no-root; \
        else \
            echo "DEV is false" && $VIRTUAL_ENV/bin/poetry install --without dev --no-root; \
        fi'

## -------------------------------- Production Stage ---------------------------- ##
FROM python:3.14-slim-bookworm AS production

# Copy venv and project files from build stage
COPY --from=build ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY --from=build /app/README.md /app/README.md
COPY --from=build /app/src /app/src
COPY --from=build /app/pyproject.toml /app/pyproject.toml

# Create non-root user for the app
RUN useradd --create-home appuser && \
    chown -R appuser:appuser /app

# Activate venv by adding it to PATH
ENV VIRTUAL_ENV=$VIRTUAL_ENV \
    PATH="$VIRTUAL_ENV/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser
# Run from the project's src directory so `python -m main` resolves to src/main.py
WORKDIR /app/src

# Default entrypoint: start the main application
# ENTRYPOINT ["python", "-m", "main"]
ENTRYPOINT [ "sleep", "infinity" ]