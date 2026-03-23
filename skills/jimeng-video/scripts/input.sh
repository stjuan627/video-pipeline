#!/bin/bash
set -euo pipefail

EDITOR_REF=${1:-}
PROMPT_TEMPLATE=${2:-}
shift 2 || true
FILES=("$@")

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SMART_UPLOAD_JS="$SCRIPT_DIR/smart_upload.js"

if [[ -z "$EDITOR_REF" || -z "$PROMPT_TEMPLATE" ]]; then
  echo "Usage: input.sh <EDITOR_REF> <PROMPT_TEMPLATE> [ABSOLUTE_FILE_PATH ...]" >&2
  exit 1
fi

if [[ ! -f "$SMART_UPLOAD_JS" ]]; then
  echo "Missing helper script: $SMART_UPLOAD_JS" >&2
  exit 1
fi

for file in "${FILES[@]}"; do
  if [[ "$file" != /* ]]; then
    echo "File path must be absolute: $file" >&2
    exit 1
  fi
  if [[ ! -f "$file" ]]; then
    echo "File not found: $file" >&2
    exit 1
  fi
done

if [[ ${#FILES[@]} -gt 0 ]]; then
  agent-browser eval "if(!document.getElementById('real-bridge')){const i=document.createElement('input');i.type='file';i.id='real-bridge';i.multiple=true;i.style.display='none';document.body.appendChild(i);}"
  agent-browser upload "#real-bridge" "${FILES[@]}"
  agent-browser eval "$(cat "$SMART_UPLOAD_JS"); performJimengUpload();"

  echo "--- [Input] Waiting for previews ---"
  agent-browser wait 5000
fi

echo "--- [Input] Typing Prompt with Refs ---"
agent-browser fill "$EDITOR_REF" ""
printf '%s\n' "$PROMPT_TEMPLATE" | sed 's/@图片[0-9][0-9]*/\
&\
/g' | while IFS= read -r line; do
  if [[ "$line" =~ ^@图片[0-9]+$ ]]; then
    label=${line#@}
    echo "Typing reference: $line"
    agent-browser type "$EDITOR_REF" "@"
    agent-browser wait 1500
    agent-browser find role option click --name "$label"
    agent-browser wait 800
  elif [[ -n "$line" ]]; then
    echo "Typing text: $line"
    agent-browser type "$EDITOR_REF" "$line"
  fi
done

echo "--- [Input] Finished ---"
