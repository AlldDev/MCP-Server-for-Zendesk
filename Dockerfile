FROM python:3.11-slim

WORKDIR /app

# README.md is required at build time: pyproject.toml declares it as the package readme.
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

RUN useradd -m appuser
USER appuser

EXPOSE 8080

CMD ["uvicorn", "mcp_zendesk.server:app", "--host", "0.0.0.0", "--port", "8080"]
