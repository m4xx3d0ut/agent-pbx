FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git tini tmux \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin agent-pbx

WORKDIR /build
COPY pyproject.toml README.md LICENSE /build/
COPY src /build/src
COPY ops/web-demo/entrypoint.sh /tmp/agent-pbx-web-demo
RUN python -m pip install --no-cache-dir ".[web-demo]" \
    && install -D -m 0755 /tmp/agent-pbx-web-demo /usr/local/bin/agent-pbx-web-demo \
    && rm -f /tmp/agent-pbx-web-demo \
    && rm -rf /build

USER 10001:10001
WORKDIR /home/agent-pbx
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read()"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/agent-pbx-web-demo"]
