#!/bin/sh
set -eu

python_bin=${PYTHON_BIN:-python}
workers=${AGENT_PBX_TEST_WORKERS:-4}
distribution=${AGENT_PBX_TEST_DIST:-worksteal}
mode=${1:-parallel}

case "$mode" in
    parallel)
        "$python_bin" -m pytest -q \
            -n "$workers" \
            --dist "$distribution" \
            --max-worker-restart 0 \
            -m "not serial"
        "$python_bin" -m pytest -q -n 0 -m serial
        ;;
    --parallel-only)
        "$python_bin" -m pytest -q \
            -n "$workers" \
            --dist "$distribution" \
            --max-worker-restart 0 \
            -m "not serial"
        ;;
    --serial-only)
        "$python_bin" -m pytest -q -n 0 -m serial
        ;;
    --serial)
        "$python_bin" -m pytest -q -n 0
        ;;
    *)
        echo "usage: $0 [--parallel-only|--serial-only|--serial]" >&2
        exit 2
        ;;
esac
