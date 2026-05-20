FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

EXPOSE 8765
CMD ["agent-pbx", "serve", "--host", "0.0.0.0", "--port", "8765", "--db", "/data/agent-pbx.sqlite"]
