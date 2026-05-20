FROM python:3.10-slim

ARG AGENT_PBX_TUI_FLASH=0
ARG AGENT_PBX_TUI_BELL=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_PBX_TUI_FLASH=${AGENT_PBX_TUI_FLASH} \
    AGENT_PBX_TUI_BELL=${AGENT_PBX_TUI_BELL}

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

EXPOSE 8765
CMD ["agent-pbx", "serve", "--host", "0.0.0.0", "--port", "8765", "--db", "/data/agent-pbx.sqlite"]
