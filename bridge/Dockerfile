# Build context: the repository root, where the workspace's uv.lock lives. The root .dockerignore
# admits only what this build reads.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# COPY . would give /app the context root's own mode and owner, which differ between builders.
COPY pyproject.toml uv.lock /app/
COPY bridge/pyproject.toml /app/bridge/
COPY bridge/src /app/bridge/src

ENV UV_PYTHON_PREFERENCE=only-system \
    UV_COMPILE_BYTECODE=1
RUN uv sync --frozen --package notification-bridge --no-dev --no-editable --no-cache

# The bridge's one writable path is its state directory; a volume mounted there replaces it.
RUN groupadd --gid 10001 bridge \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin bridge \
    && mkdir /var/lib/notification-bridge \
    && chown bridge:bridge /var/lib/notification-bridge
USER 10001:10001

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8090
ENTRYPOINT ["tini", "--"]
CMD ["notification-bridge"]
