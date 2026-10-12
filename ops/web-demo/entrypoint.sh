#!/bin/sh
set -eu

public_url=${AGENT_PBX_DEMO_PUBLIC_URL:-}
allowed_origins=${AGENT_PBX_DEMO_ALLOWED_ORIGINS:-}
max_sessions=${AGENT_PBX_DEMO_MAX_SESSIONS:-8}
session_ttl=${AGENT_PBX_DEMO_SESSION_TTL_SECONDS:-600}

if [ -z "$public_url" ]; then
    echo "AGENT_PBX_DEMO_PUBLIC_URL must be an externally visible HTTPS URL" >&2
    exit 2
fi

set -- demo web \
    --host 0.0.0.0 \
    --port 8000 \
    --allow-public-bind \
    --public-url "$public_url" \
    --max-sessions "$max_sessions" \
    --session-ttl-seconds "$session_ttl"

old_ifs=$IFS
IFS=,
for origin in $allowed_origins; do
    if [ -n "$origin" ]; then
        set -- "$@" --allowed-origin "$origin"
    fi
done
IFS=$old_ifs

exec agent-pbx "$@"
