#!/bin/bash
set -euo pipefail

RATIO_REF=${1:-}
RATIO_VAL=${2:-}

if [[ -z "$RATIO_REF" || -z "$RATIO_VAL" ]]; then
  echo "Usage: fix.sh <RATIO_REF> <RATIO_VALUE>" >&2
  exit 1
fi

echo "--- [Fix] Resetting Ratio to $RATIO_VAL ---"
agent-browser click "@$RATIO_REF" && agent-browser wait 1200
agent-browser find role label click --name "$RATIO_VAL"
agent-browser wait 1000
agent-browser click "body"
