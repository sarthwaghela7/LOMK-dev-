FROM node:22-bookworm-slim AS calculator-runtime
FROM python:3.12-slim-bookworm
COPY --from=calculator-runtime /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --system --no-create-home appuser && chown -R appuser /app
USER appuser
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
