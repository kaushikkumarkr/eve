FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations

RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --extra azure

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["agency-mcp"]
