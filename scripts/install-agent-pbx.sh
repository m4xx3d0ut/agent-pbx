#!/usr/bin/env sh
set -eu

DEFAULT_BASE_URL="https://github.com/m4xx3d0ut/agent-pbx/releases/latest/download"
BASE_URL="${AGENT_PBX_INSTALL_BASE_URL:-$DEFAULT_BASE_URL}"
ARCHIVE_NAME="${AGENT_PBX_WHEELHOUSE_ARCHIVE:-agent-pbx-wheelhouse.tar.gz}"
FORCE_STANDALONE="${AGENT_PBX_FORCE_STANDALONE:-0}"
INSTALL_DIR="${AGENT_PBX_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/agent-pbx}"
BIN_DIR="${AGENT_PBX_BIN_DIR:-$HOME/.local/bin}"

log() {
  printf '%s\n' "$*"
}

fail() {
  printf 'agent-pbx install: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

fetch() {
  url="$1"
  dest="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$dest"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$url" -O "$dest"
  else
    fail "missing curl or wget"
  fi
}

python_bin="${PYTHON:-python3}"
need_cmd "$python_bin"
need_cmd mktemp
need_cmd tar
"$python_bin" - <<'PY' || fail "Python 3.10 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT INT TERM

archive="$tmp_dir/$ARCHIVE_NAME"
wheelhouse="$tmp_dir/wheelhouse"
log "Downloading Agent PBX wheelhouse from $BASE_URL/$ARCHIVE_NAME"
fetch "$BASE_URL/$ARCHIVE_NAME" "$archive"
mkdir -p "$wheelhouse"
tar -xzf "$archive" -C "$wheelhouse"
if [ -d "$wheelhouse/agent-pbx-wheelhouse" ]; then
  wheelhouse="$wheelhouse/agent-pbx-wheelhouse"
fi

if [ "${VIRTUAL_ENV:-}" != "" ] && [ "$FORCE_STANDALONE" != "1" ]; then
  target_python="$VIRTUAL_ENV/bin/python"
  install_mode="active-venv"
else
  target_python="$INSTALL_DIR/venv/bin/python"
  install_mode="standalone"
  mkdir -p "$INSTALL_DIR"
  "$python_bin" -m venv "$INSTALL_DIR/venv" || fail "failed to create venv; install the Python venv package or activate an existing venv"
fi

package_spec="agent-pbx"
for candidate in "$wheelhouse"/agent_pbx-*.whl; do
  [ -f "$candidate" ] || continue
  wheel_base="$(basename "$candidate")"
  package_version="${wheel_base#agent_pbx-}"
  package_version="${package_version%%-*}"
  if [ "$package_version" != "" ]; then
    package_spec="agent-pbx==$package_version"
  fi
  break
done

"$target_python" -m pip install --upgrade pip >/dev/null
if ! "$target_python" -m pip install --no-compile --no-index --find-links "$wheelhouse" "$package_spec"; then
  log ""
  log "Bundled wheelhouse install failed; retrying with package index access for platform-specific wheels."
  "$target_python" -m pip install --no-compile --find-links "$wheelhouse" "$package_spec"
fi

if [ "$install_mode" = "standalone" ]; then
  mkdir -p "$BIN_DIR"
  cat >"$BIN_DIR/agent-pbx" <<EOF
#!/usr/bin/env sh
exec "$target_python" -m agent_pbx "\$@"
EOF
  chmod +x "$BIN_DIR/agent-pbx"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
      log ""
      log "Add Agent PBX to your PATH:"
      log "  export PATH=\"$BIN_DIR:\$PATH\""
      ;;
  esac
fi

agent_pbx_cmd="$target_python -m agent_pbx"
if command -v agent-pbx >/dev/null 2>&1; then
  agent_pbx_cmd="agent-pbx"
fi

log ""
log "Agent PBX installed ($install_mode)."
log "Verify with: $agent_pbx_cmd --version"
