---
name: jimeng-video
description: Automate Jimeng web video generation with agent-browser plus bundled shell scripts. Use when the user wants to use 即梦/Jimeng to generate a video in the browser, choose Seedance model or sub-mode, upload one or more reference images, type prompts with `@图片N` references, or recover a fragile Jimeng UI workflow without hand-driving raw browser commands.
---

# Jimeng Video

Use this skill to drive the fragile Jimeng web UI through bundled scripts instead of hand-written browser clicks.

## Quick start

`{SKILL_DIR}` means the directory that contains this `SKILL.md` file.

Bundled commands:

- `{SKILL_DIR}/scripts/prepare.sh`
- `{SKILL_DIR}/scripts/input.sh`
- `{SKILL_DIR}/scripts/fix.sh`
- `{SKILL_DIR}/scripts/submit_and_capture.sh`

Prerequisites:

- `agent-browser` is installed and working.
- The Jimeng site is already logged in.
- Any upload path has been converted to an absolute path.

## Request mapping

Extract these defaults from the user's message:

| Parameter | Default | Mapping |
| --- | --- | --- |
| `model` | `Seedance 2.0 Fast` | `seedance fast` / `快速` -> `Seedance 2.0 Fast`; `seedance` / `即梦` / unspecified -> `Seedance 2.0` unless speed is clearly preferred |
| `sub-mode` | `全能参考` | `全能参考` / `多图参考` -> `全能参考`; `首尾帧` -> `首尾帧` |
| `ratio` | `9:16` | `竖版` -> `9:16`; `横版` -> `16:9`; `方形` -> `1:1` |
| `duration` | `10s` | `5秒` -> `5s`; `10秒` -> `10s`; `15秒` -> `15s` |
| `prompt` | required | Preserve `@图片N` markers when the prompt refers to uploaded images |
| `images` | optional | Convert every path to an absolute path before invoking `input.sh` |

## Mandatory workflow

1. Open Jimeng with `agent-browser open "https://jimeng.jianying.com/ai-tool/generate?type=video"`.
2. Run `agent-browser snapshot -i` and capture `MODEL_REF`, `SUBMODE_REF`, `RATIO_REF`, `DURATION_REF`.
3. Run:

```bash
"{SKILL_DIR}/scripts/prepare.sh" <MODEL_REF> <SUBMODE_REF> <RATIO_REF> <DURATION_REF> "<MODEL>" "<SUB_MODE>" "<RATIO>" "<DURATION>"
```

4. Take a fresh snapshot because `prepare.sh` may re-render the editor and invalidate the old refs. Capture `EDITOR_REF`.
5. Run:

```bash
"{SKILL_DIR}/scripts/input.sh" <EDITOR_REF> "<PROMPT_WITH_OPTIONAL_@图片N>" "/absolute/path/to/image1.webp" "/absolute/path/to/image2.webp"
```

6. Take a fresh snapshot because upload may invalidate refs. Capture `NEW_EDITOR_REF` and `NEW_RATIO_REF`.
7. Run:

```bash
"{SKILL_DIR}/scripts/fix.sh" <NEW_RATIO_REF> "<RATIO>"
```

8. Submit from the editor textbox and capture the returned `submit_id`:

```bash
"{SKILL_DIR}/scripts/submit_and_capture.sh" <NEW_EDITOR_REF>
```

This script first captures the current first `[data-id]` as `id1`, then focuses the editor textbox and presses Enter to submit, immediately captures the first `[data-id]` again as `id2`, and returns success right away when `id2 != id1`. If they are still equal, it keeps polling every 3 seconds by default until the first `[data-id]` changes. Read `data.submitId` from the output JSON.

`submit_and_capture.sh` is implemented inside this skill directory and no longer relies on the older repository-level wrapper.

## Rules that matter

- Agent is the eyes; scripts are the hands.
- Do not use raw `agent-browser click`, `fill`, or `type` for core configuration when one of the bundled scripts covers that step.
- `input.sh` rejects relative file paths on purpose. Fix the path instead of bypassing the check.
- Re-snapshot after `prepare.sh`, after upload, and whenever Jimeng changes the UI before reusing any old ref.
- Always run ratio correction after upload, even if the ratio looked right earlier.
- Use the latest editor ref for submission, because upload or rerender may invalidate the earlier textbox ref.

## Example

```bash
agent-browser open "https://jimeng.jianying.com/ai-tool/generate?type=video"
agent-browser snapshot -i
# identify refs: MODEL_REF SUBMODE_REF RATIO_REF DURATION_REF

"{SKILL_DIR}/scripts/prepare.sh" e3 e5 e8 e12 "Seedance 2.0 Fast" "全能参考" "9:16" "10s"
agent-browser snapshot -i
# identify ref: EDITOR_REF
"{SKILL_DIR}/scripts/input.sh" e15 "橘猫参考@图片1, 驴参考@图片2. 两只动物在草地上追逐嬉戏" "/absolute/path/cat.webp" "/absolute/path/donkey.webp"
agent-browser snapshot -i
# identify refs: NEW_EDITOR_REF NEW_RATIO_REF
"{SKILL_DIR}/scripts/fix.sh" e22 "9:16"
"{SKILL_DIR}/scripts/submit_and_capture.sh" e19
```

## References

- Read `references/workflow.md` when you need the compact troubleshooting table or a reminder of the mandatory execution order.
