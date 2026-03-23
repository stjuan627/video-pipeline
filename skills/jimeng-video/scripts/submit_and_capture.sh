#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
EDITOR_REF=${1:-}
TIMEOUT_SECONDS=${2:-20}
POLL_INTERVAL_SECONDS=${3:-3}
BASELINE_SETTLE_MS=${4:-300}

if [[ -z "$EDITOR_REF" ]]; then
  echo "Usage: submit_and_capture.sh <EDITOR_REF> [TIMEOUT_SECONDS] [POLL_INTERVAL_SECONDS] [BASELINE_SETTLE_MS]" >&2
  exit 1
fi

if ! [[ "$TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$TIMEOUT_SECONDS" -le 0 ]; then
  echo "TIMEOUT_SECONDS must be a positive integer" >&2
  exit 1
fi

if ! [[ "$POLL_INTERVAL_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "POLL_INTERVAL_SECONDS must be numeric" >&2
  exit 1
fi

if ! [[ "$BASELINE_SETTLE_MS" =~ ^[0-9]+$ ]]; then
  echo "BASELINE_SETTLE_MS must be a non-negative integer" >&2
  exit 1
fi

capture_state() {
  agent-browser eval --json --stdin < "$SCRIPT_DIR/get_first_data_id.js"
}

extract_id() {
  printf '%s\n' "$1" | jq -r '.data.result.firstDataId // empty'
}

submit_from_editor() {
  agent-browser focus "@$EDITOR_REF"
  agent-browser press Enter
}

BASELINE_JSON=$(capture_state)
BASELINE_ID=$(extract_id "$BASELINE_JSON")

if [ "$BASELINE_SETTLE_MS" -gt 0 ]; then
  agent-browser wait "$BASELINE_SETTLE_MS" >/dev/null
fi

BASELINE_CONFIRM_JSON=$(capture_state)
BASELINE_CONFIRM_ID=$(extract_id "$BASELINE_CONFIRM_JSON")
if [ -n "$BASELINE_CONFIRM_ID" ]; then
  BASELINE_JSON="$BASELINE_CONFIRM_JSON"
  BASELINE_ID="$BASELINE_CONFIRM_ID"
fi

submit_from_editor

POST_SUBMIT_JSON=$(capture_state)
POST_SUBMIT_ID=$(extract_id "$POST_SUBMIT_JSON")

if [ -n "$POST_SUBMIT_ID" ] && [ "$POST_SUBMIT_ID" != "$BASELINE_ID" ]; then
  jq -n \
    --arg submitId "$POST_SUBMIT_ID" \
    --arg previousId "$BASELINE_ID" \
    --argjson baseline "$BASELINE_JSON" \
    --argjson current "$POST_SUBMIT_JSON" \
    '{success:true,data:{submitId:$submitId,previousId:($previousId | select(length > 0)),baseline:$baseline.data.result,current:$current.data.result,detection:"post_submit_snapshot"}}'
  exit 0
fi

START_TIME=$(date +%s)

while true; do
  STATE_JSON=$(capture_state)
  CURRENT_ID=$(extract_id "$STATE_JSON")

  if [ -n "$CURRENT_ID" ] && [ "$CURRENT_ID" != "$BASELINE_ID" ]; then
    jq -n \
      --arg submitId "$CURRENT_ID" \
      --arg previousId "$BASELINE_ID" \
      --argjson baseline "$BASELINE_JSON" \
      --argjson current "$STATE_JSON" \
      '{success:true,data:{submitId:$submitId,previousId:($previousId | select(length > 0)),baseline:$baseline.data.result,current:$current.data.result,detection:"polling"}}'
    exit 0
  fi

  NOW=$(date +%s)
  ELAPSED=$((NOW - START_TIME))
  if [ "$ELAPSED" -ge "$TIMEOUT_SECONDS" ]; then
    echo "Timed out waiting for first [data-id] to change after ${TIMEOUT_SECONDS}s" >&2
    jq -n \
      --arg previousId "$BASELINE_ID" \
        --arg postSubmitId "$POST_SUBMIT_ID" \
        --argjson baseline "$BASELINE_JSON" \
        --argjson postSubmit "$POST_SUBMIT_JSON" \
        --argjson current "$STATE_JSON" \
        '{success:false,error:"timeout_waiting_for_new_data_id",data:{previousId:($previousId | select(length > 0)),postSubmitId:($postSubmitId | select(length > 0)),baseline:$baseline.data.result,postSubmit:$postSubmit.data.result,current:$current.data.result}}' >&2
    exit 1
  fi

  sleep "$POLL_INTERVAL_SECONDS"
done
