FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY README.md pyproject.toml /app/
COPY src /app/src

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["mcp-ibkr"]
