#!/bin/sh
# Gaussian 16 front-end script (Reconstruction — Phase 8C).
#
# This replaces the mock that Phase 5/6 installed at /opt/g16/g16. The real
# Gaussian 16 binary tree under /opt/g16/ is intact (l1.exe etc.); only the
# front-end wrapper had been clobbered.
#
# The standard Gaussian 16 wrapper sets:
#   - GAUSS_EXEDIR = directory of the binary tree (here /opt/g16)
#   - GAUSS_SCRDIR = scratch directory for checkpoint files
# and exec's $GAUSS_EXEDIR/l1.exe with the supplied arguments.
#
# The original Gaussian, Inc. wrapper is part of the commercial distribution;
# this is a clean-room reconstruction based on documented behaviour.
#
set -e

# Resolve the directory holding this script so the binary tree can be located
# regardless of the user's CWD. If we're invoked through a symlink, follow it
# first so GAUSS_EXEDIR points at the real binary tree, not the symlink dir.
SELF_PATH="$0"
case "$SELF_PATH" in
    /*) ;;                           # already absolute
    *)  SELF_PATH="$(command -v "$0" 2>/dev/null || echo "$0")" ;;
esac
# If SELF_PATH is a symlink, chase it (one hop is enough for our use).
if [ -L "$SELF_PATH" ]; then
    TARGET=$(readlink -f "$SELF_PATH" 2>/dev/null || ls -l "$SELF_PATH" | sed 's/.*-> //')
    case "$TARGET" in
        /*) SELF_PATH="$TARGET" ;;
        *)  SELF_PATH="$(dirname "$SELF_PATH")/$TARGET" ;;
    esac
fi
SELF_DIR="$(cd "$(dirname "$SELF_PATH")" && pwd)"
export GAUSS_EXEDIR="${GAUSS_EXEDIR:-$SELF_DIR}"

# Scratch dir — default to /tmp/g16_scratch but allow override.
: "${GAUSS_SCRDIR:=/tmp/g16_scratch}"
mkdir -p "$GAUSS_SCRDIR"
export GAUSS_SCRDIR

# Number of cores — used by the parallel Linda binaries (l101.exe etc.).
: "${NProcShared:=1}"
export NProcShared

# Sanity-check that the binary tree looks like a Gaussian install.
if [ ! -x "$GAUSS_EXEDIR/l1.exe" ]; then
    echo "Gaussian 16 binary tree incomplete: $GAUSS_EXEDIR/l1.exe missing" >&2
    echo "Set GAUSS_EXEDIR to the directory containing l1.exe." >&2
    exit 127
fi

# Run the binary. l1.exe reads a single .gjf filename and writes a .log.
exec "$GAUSS_EXEDIR/l1.exe" "$@"