#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <distribution-output>" >&2
    exit 64
fi

exec python3 "$SCRIPT_DIR/verify_artifact.py" \
    --output "$1" \
    --source-lock "$REPOSITORY_ROOT/source.lock.json" \
    --environment-lock "$REPOSITORY_ROOT/build-environment.lock.json" \
    --tracked-swift "$REPOSITORY_ROOT/Sources/FCastSenderSDK/FCastSenderSDK.swift"
