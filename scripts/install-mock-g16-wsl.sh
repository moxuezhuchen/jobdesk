#!/usr/bin/env bash
# Install the mock Gaussian binary into WSL at /opt/g16/g16.
#
# Used by Phase 6 smoke tests; not for production.
set -eu

MOCK_SRC="$(dirname "$(realpath "$0")")/g16"
WSL_DEST="/opt/g16/g16"

wsl bash -c "
  set -e
  if [ ! -d /opt/g16 ]; then
    mkdir -p /opt/g16
  fi
  # Install (or refresh) the mock g16
  cat > '$WSL_DEST' <<'MOCK_EOF'
$(cat "$MOCK_SRC")
MOCK_EOF
  chmod +x '$WSL_DEST'
  ln -sf '$WSL_DEST' /usr/local/bin/g16
  echo '[install-mock-g16] installed at $WSL_DEST and /usr/local/bin/g16'
"
