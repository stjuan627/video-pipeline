# Jimeng Workflow Reference

## Core rules

1. Agent is the eyes; scripts are the hands.
2. Never use raw `agent-browser click/fill/type` for core parameter setup when a bundled script exists.
3. Convert every upload path to an absolute path before calling `input.sh`.
4. Re-run `agent-browser snapshot -i` after `prepare.sh` and after uploads before reusing any ref.
5. Always run ratio correction after upload, because Jimeng may reset it to auto.

## Mandatory execution path

1. Open `https://jimeng.jianying.com/ai-tool/generate?type=video`
2. Run `agent-browser snapshot -i`
3. Record `MODEL_REF`, `SUBMODE_REF`, `RATIO_REF`, `DURATION_REF`
4. Run `{SKILL_DIR}/scripts/prepare.sh`
5. Run `agent-browser snapshot -i` again and record `EDITOR_REF`
6. Run `{SKILL_DIR}/scripts/input.sh`
7. Run `agent-browser snapshot -i` again and record `NEW_EDITOR_REF` and `NEW_RATIO_REF`
8. Run `{SKILL_DIR}/scripts/fix.sh`
9. Run `{SKILL_DIR}/scripts/submit_and_capture.sh <NEW_EDITOR_REF>`; it captures `id1` before submit, focuses the editor textbox, presses Enter, captures `id2` right after submit, and treats `id2 != id1` as immediate success before falling back to polling

## Parameter table

| Script | Required args | Purpose |
| --- | --- | --- |
| `prepare.sh` | 4 refs + model + sub-mode + ratio + duration | Initialize model, mode, ratio, duration |
| `input.sh` | editor ref + prompt + optional absolute image paths | Upload files and type prompt with `@图片N` references |
| `fix.sh` | ratio ref + ratio value | Restore target ratio after upload |
| `submit_and_capture.sh` | latest editor ref + optional timeout/polling args | Focus the editor, press Enter, and capture the new submit id |

## Failure handling

- `Unknown ref` or page flash/crash after `prepare.sh`: take a fresh snapshot and retry with the new ref.
- Browser crash during upload: check whether any image path is relative.
- Upload did not attach: confirm `input.sh` ran with existing files and wait for preview cards.
- Ratio changed unexpectedly: run `fix.sh` again with the latest ratio ref.
- Submit polling loops on the same id: use the skill-local `submit_and_capture.sh` with the latest editor ref; it records a pre-submit baseline, performs an immediate post-submit comparison after `focus + Enter`, and returns richer timeout diagnostics.
