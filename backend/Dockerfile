FROM python:3.12-slim

WORKDIR /app

# Najpierw zależności (warstwa cache'owana, dopóki pyproject się nie zmieni)
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[gemini]"

COPY data ./data
COPY web ./web

# Baza SQLite na wolumenie - przetrwa restart i aktualizację kontenera
ENV DATABASE_PATH=/app/storage/bezbarier.db
RUN mkdir -p /app/storage
VOLUME /app/storage

EXPOSE 8000
CMD ["uvicorn", "bezbarier.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
