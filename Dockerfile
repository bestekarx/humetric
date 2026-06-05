FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY src/ src/
COPY packs/ packs/
COPY prompts/ prompts/

EXPOSE 8002

CMD ["uvicorn", "humetric.api:app", "--host", "0.0.0.0", "--port", "8002"]
