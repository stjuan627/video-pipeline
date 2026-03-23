#!/bin/bash
set -euo pipefail

MODEL_REF=${1:-}
SUB_REF=${2:-}
RATIO_REF=${3:-}
DUR_REF=${4:-}
MODEL_NAME=${5:-}
SUB_NAME=${6:-}
RATIO_VAL=${7:-}
DUR_VAL=${8:-}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_JS="$SCRIPT_DIR/lib.js"

if [[ -z "$MODEL_REF" || -z "$SUB_REF" || -z "$RATIO_REF" || -z "$DUR_REF" || -z "$MODEL_NAME" || -z "$SUB_NAME" || -z "$RATIO_VAL" || -z "$DUR_VAL" ]]; then
  echo "Usage: prepare.sh <MODEL_REF> <SUBMODE_REF> <RATIO_REF> <DURATION_REF> <MODEL_NAME> <SUBMODE_NAME> <RATIO> <DURATION>" >&2
  exit 1
fi

if [[ -f "$LIB_JS" ]]; then
  echo "--- [Prepare] Injecting Library ---"
  agent-browser eval "$(cat "$LIB_JS")"
fi

echo "--- [Prepare] Setting Model: $MODEL_NAME ---"
agent-browser click "@$MODEL_REF" && agent-browser wait 1000
agent-browser find role option click --name "$MODEL_NAME"

echo "--- [Prepare] Setting Sub-mode: $SUB_NAME ---"
agent-browser click "@$SUB_REF" && agent-browser wait 1000
agent-browser find role option click --name "$SUB_NAME"

echo "--- [Prepare] Setting Ratio: $RATIO_VAL ---"
agent-browser click "@$RATIO_REF" && agent-browser wait 1000
agent-browser find role label click --name "$RATIO_VAL"

echo "--- [Prepare] Setting Duration: $DUR_VAL ---"
agent-browser click "@$DUR_REF" && agent-browser wait 1000
agent-browser find role option check --name "$DUR_VAL"
