---
name: image-generation
description: Generate images using AI models (grsai/kie providers). Use when the user asks to generate, create, or modify images. Supports natural language model selection, provider switching, and img2img with reference images.
---

# Image Generation Skill

Generate images by invoking `generate_image.py` via Bash. Extract parameters from the user's natural language request and map them to CLI arguments.

## Script Location

The script is at: `{SKILL_DIR}/generate_image.py`

Run with: `python3 {SKILL_DIR}/generate_image.py --prompt "..." [options]`

Where `{SKILL_DIR}` is the directory containing this skill.md file.

The script now downloads successful results into the current working directory by default, and returns the local file paths in `saved_paths`.

Before validation, the script automatically searches upward from the current working directory for `.env` files and loads any missing environment variables it finds.

## Model Alias Mapping

Map the user's natural language to `--model` values:

| User says (zh/en)                            | --model value    | Notes                                                               |
| -------------------------------------------- | ---------------- | ------------------------------------------------------------------- |
| 香蕉 / banana / 快速 / fast / default        | nano-banana-fast | Fastest, basic quality. Default model.                              |
| 香蕉pro / banana pro / 高质量 / high quality | nano-banana-pro  | High quality, supports --resolution 1K/2K/4K                        |
| 香蕉2 / banana 2 / banana v2 / 新版          | nano-banana-2    | Next generation, supports --resolution 1K/2K/4K                     |
| gpt图片 / gpt image / gpt / sora             | gpt-1.5-image    | GPT style. grsai only. Aspect ratio limited to: auto, 1:1, 2:3, 3:2 |

If the user does not specify a model, use `nano-banana-fast`.

## Provider Switching

Default provider: `grsai`

- User says "用kie" / "使用kie" / "switch to kie" → `--provider kie`
- User says "用grsai" / "使用grsai" / "switch to grsai" → `--provider grsai`
- **KIE only supports:** nano-banana-pro, nano-banana-2. If the user requests kie with an unsupported model, warn them and suggest a compatible model.

## Parameter Extraction

From the user's message, extract these CLI arguments:

| Parameter        | CLI flag        | Default                   | How to extract                                                                                                      |
| ---------------- | --------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Prompt           | --prompt        | (required)                | The image description text                                                                                          |
| Model            | --model         | nano-banana-fast          | See alias mapping above                                                                                             |
| Provider         | --provider      | grsai                     | See provider switching above                                                                                        |
| Aspect ratio     | --aspect-ratio  | 9:16                      | "16:9" / "横版" → 16:9, "1:1" / "方形" / "正方形" → 1:1, "竖版" → 9:16                                              |
| Resolution       | --resolution    | 2K                        | "4K" / "高清" / "超清" → 4K, "1K" → 1K. Only affects nano-banana-pro/2.                                             |
| Reference images | --source-images | (none)                    | Image URLs from conversation context or user message                                                                |
| Count            | --count         | 1                         | "两张" → 2, "三张" → 3, "四张" → 4. Max 4.                                                                          |
| Output directory | --output-dir    | current working directory | "保存到当前目录" / "保存到这里" → `.`, "保存到 apps/starter/public" / "下载到 /tmp/images" → use that path directly |
| Timeout          | --timeout       | 300                       | Usually not specified by user. Increase for slow models.                                                            |

Directory mapping rules:

- If the user does not mention where to save, do not pass `--output-dir`; let the script default to the current working directory.
- If the user says "保存到当前目录" / "保存到当前工作目录" / "下载到这里", map to `--output-dir .`
- If the user names a relative path such as `apps/starter/public`, pass it directly as `--output-dir "apps/starter/public"`
- If the user names an absolute path such as `/tmp/images`, pass it directly as `--output-dir "/tmp/images"`
- Mention the final saved local path(s) from `saved_paths` in your reply.

## Context Image Handling (img2img)

When the user wants to modify or reference existing images:

1. Look for image URLs in the conversation (previously generated results, user-provided URLs)
2. Pass them as `--source-images url1 url2`
3. Trigger phrases: "基于这张图" / "改一下上面的图" / "参考这张" / "based on this image"

## Running the Script

Example invocations:

```bash
# Basic: "生成一张日落海滩的图"
python3 {SKILL_DIR}/generate_image.py --prompt "日落海滩，金色阳光洒在沙滩上"

# With model: "用香蕉pro生成两张9:16的图"
python3 {SKILL_DIR}/generate_image.py --prompt "..." --model nano-banana-pro --aspect-ratio 9:16 --count 2

# With kie: "用kie的banana2生成一张4K的图"
python3 {SKILL_DIR}/generate_image.py --prompt "..." --model nano-banana-2 --provider kie --resolution 4K

# img2img: "基于上面那张图，改成夜景"
python3 {SKILL_DIR}/generate_image.py --prompt "夜景版本" --source-images "https://prev-result.png"

# Save to a specific folder: "生成后保存到 apps/starter/public"
python3 {SKILL_DIR}/generate_image.py --prompt "..." --output-dir "apps/starter/public"
```

## Handling Output

The script outputs JSON to stdout.

**On success** (`status: "success"`):

- Display each URL as a markdown image: `![Generated Image](url)`
- Mention the local saved file path(s) from `saved_paths`
- If multiple images, display each one with a number

**On partial success** (`status: "partial"`):

- Display successful images
- Mention any successfully saved local file path(s) from `saved_paths`
- Mention that some failed and show the error

**On error** (`status: "error"`):

- Display the error message to the user
- If the error mentions "moderation", suggest the user adjust their prompt

**On config error** (`status: "config_error"`):

- Tell the user which environment variable needs to be set

## Environment Variables Required

- `GRSAI_API_KEY` — required for grsai provider
- `KIE_API_KEY` — required for kie provider
- `GRSAI_OSS_ID` — optional, for grsai OSS storage
- `GRSAI_OSS_PATH` — optional, defaults to "aigc"

If these values already exist in the shell environment, they take priority over anything loaded from `.env`.
