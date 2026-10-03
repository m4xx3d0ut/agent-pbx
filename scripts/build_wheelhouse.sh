#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/dist/agent-pbx-wheelhouse"
PYTHON_BIN="${PYTHON_BIN:-python}"

usage() {
  cat <<'EOF'
usage: scripts/build_wheelhouse.sh [--out PATH] [--python PYTHON]

Build a local wheelhouse containing agent-pbx and its Python dependencies.

Install with:
  python -m pip install --no-index --find-links <wheelhouse> agent-pbx
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      OUT_DIR="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

OUT_PARENT="$(mkdir -p "$(dirname "$OUT_DIR")" && cd "$(dirname "$OUT_DIR")" && pwd)"
OUT_DIR="${OUT_PARENT}/$(basename "$OUT_DIR")"
ARCHIVE="${OUT_PARENT}/agent-pbx-wheelhouse.tar.gz"
INSTALLER="${OUT_PARENT}/install-agent-pbx.sh"

rm -rf "$OUT_DIR" "$ARCHIVE" "${ARCHIVE}.sha256" "$INSTALLER" "${ROOT_DIR}/build"
mkdir -p "$OUT_DIR"

"${PYTHON_BIN}" -m pip wheel --wheel-dir "$OUT_DIR" "$ROOT_DIR"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/release_manifest.py" generate --wheelhouse "$OUT_DIR" >/dev/null

cat >"${OUT_DIR}/INSTALL.txt" <<EOF
Install Agent PBX from this wheelhouse:

  python -m pip install --no-index --find-links ${OUT_DIR} agent-pbx

Run locally:

  agent-pbx mcp start
  agent-pbx mcp status
EOF

echo "wheelhouse: ${OUT_DIR}"
tar -C "$(dirname "$OUT_DIR")" -czf "$ARCHIVE" "$(basename "$OUT_DIR")"
echo "archive: ${ARCHIVE}"
"${PYTHON_BIN}" - "$ARCHIVE" <<'PY'
from pathlib import Path
import hashlib
import sys

path = Path(sys.argv[1])
digest = hashlib.sha256(path.read_bytes()).hexdigest()
path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
PY
echo "archive checksum: ${ARCHIVE}.sha256"
cp "${ROOT_DIR}/scripts/install-agent-pbx.sh" "$INSTALLER"
chmod +x "$INSTALLER"
echo "installer: ${INSTALLER}"
