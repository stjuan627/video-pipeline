---
name: video-generation
description: Generate videos using AI video models across grsai and kie providers. Use this whenever the user asks to generate, create, or modify a video from text, an image, first/last frames, or reference images. Supports natural language model selection, provider switching, parameter mapping, and automatic local download of the final video.
---

# Video Generation Skill

Generate videos by invoking `generate_video.py` via Bash. Extract parameters from the user's natural language request and map them to CLI arguments.

## Script Location

The script is at: `{SKILL_DIR}/generate_video.py`

Run with: `python3 {SKILL_DIR}/generate_video.py --prompt "..." [options]`

Where `{SKILL_DIR}` is the directory containing this skill file.

The script downloads successful results into the current working directory by default, and returns the local file paths in `saved_paths`.

Before validation, the script automatically searches upward from the current working directory for `.env` files and loads any missing environment variables it finds.

## Model Alias Mapping

Map the user's natural language to `--model` values:

| User says (zh/en)                                                 | --model value         | Notes                                                   |
| ----------------------------------------------------------------- | --------------------- | ------------------------------------------------------- |
| veo / veo fast / google veo / 默认视频 / default video / 快速视频 | veo3.1-fast           | Default model. Supports grsai and kie.                  |
| veo pro / veo quality / 高质量视频 / 高画质veo                    | veo3.1-pro            | High quality. Use grsai unless the user says otherwise. |
| sora / sora2 / sora 2                                             | sora-2                | Supports grsai and kie.                                 |
| hailuo / 海螺                                                     | hailuo-2.3            | Kie only.                                               |
| hailuo fast / 海螺极速                                            | hailuo-2.3-fast       | Kie only.                                               |
| kling / 可灵                                                      | kling-2.6             | Kie only.                                               |
| grok                                                              | grok-imagine          | Kie only.                                               |
| seedance / seedance pro                                           | seedance-1.0-pro      | Kie only.                                               |
| seedance fast / seedance pro fast                                 | seedance-1.0-pro-fast | Kie only.                                               |
| mock video / mock                                                 | mock-video-lab        | Mock provider for development only.                     |

If the user does not specify a model, use `veo3.1-fast`.

## Provider Switching

Default provider: `grsai`

- User says "用kie" / "使用kie" / "switch to kie" -> `--provider kie`
- User says "用grsai" / "使用grsai" / "switch to grsai" -> `--provider grsai`
- User says "用mock" / "使用mock" -> `--provider mock`

Compatibility rules:

- `grsai` supports: `veo3.1-fast`, `veo3.1-pro`, `sora-2`
- `kie` supports: `veo3.1-fast`, `sora-2`, `hailuo-2.3`, `hailuo-2.3-fast`, `kling-2.6`, `grok-imagine`, `seedance-1.0-pro`, `seedance-1.0-pro-fast`
- `mock` supports only `mock-video-lab`

If the user requests an incompatible provider/model pair, warn them and switch to a compatible pair only if the intent is obvious. Otherwise ask.

## Parameter Extraction

From the user's message, extract these CLI arguments:

| Parameter        | CLI flag             | Default                   | How to extract                                                                              |
| ---------------- | -------------------- | ------------------------- | ------------------------------------------------------------------------------------------- |
| Prompt           | `--prompt`           | (required)                | The video description text                                                                  |
| Model            | `--model`            | `veo3.1-fast`             | See alias mapping above                                                                     |
| Provider         | `--provider`         | `grsai`                   | See provider switching above                                                                |
| Aspect ratio     | `--aspect-ratio`     | `16:9`                    | "16:9" / "横版" -> `16:9`, "9:16" / "竖版" -> `9:16`, "1:1" / "方形" -> `1:1`               |
| Duration         | `--duration`         | model default             | "5秒" -> `5`, "6s" -> `6`, "10秒" -> `10`, "15秒" -> `15`                                   |
| Resolution       | `--resolution`       | model default             | "480p" / "720p" / "768p" / "1080p" / "4k"                                                   |
| First frame      | `--first-frame-url`  | (none)                    | If the user provides a start-frame image URL or says use an image as the first frame        |
| Last frame       | `--last-frame-url`   | (none)                    | If the user provides an end-frame image URL                                                 |
| Reference images | `--reference-images` | (none)                    | Image URLs from conversation context or the user message                                    |
| Output directory | `--output-dir`       | current working directory | "保存到当前目录" / "保存到这里" -> `.`, explicit relative or absolute paths -> use directly |
| Timeout          | `--timeout`          | `600`                     | Usually not specified by user. Increase for slower models if needed.                        |

Directory mapping rules:

- If the user does not mention where to save, do not pass `--output-dir`; let the script default to the current working directory.
- If the user says "保存到当前目录" / "保存到当前工作目录" / "下载到这里", map to `--output-dir .`
- If the user names a relative path such as `apps/starter/public`, pass it directly as `--output-dir "apps/starter/public"`
- If the user names an absolute path such as `/tmp/videos`, pass it directly as `--output-dir "/tmp/videos"`
- Mention the final saved local path(s) from `saved_paths` in your reply.

## Source Media Handling

When the user wants image-to-video or frame-guided generation:

1. Look for image URLs in the conversation or the user's message.
2. If the user says "基于这张图生成视频" / "用这张图做首帧", pass it as `--first-frame-url`.
3. If the user says "以这张图结尾" / "最后一帧用这张图", pass it as `--last-frame-url`.
4. If the user says "参考这些图" / "基于这几张图", pass them as `--reference-images url1 url2 ...`.

If the user only references a local file path and no public URL is available, tell them you need an accessible image URL first.

## Running the Script

Example invocations:

```bash
# Basic: "生成一个宇航员月球漫步的视频"
python3 {SKILL_DIR}/generate_video.py --prompt "一个宇航员在月球表面缓慢行走，电影感光影"

# With model/provider: "用kie的海螺生成一个10秒竖版视频"
python3 {SKILL_DIR}/generate_video.py --prompt "..." --model hailuo-2.3 --provider kie --aspect-ratio 9:16 --duration 10

# With first frame: "基于这张图生成一个5秒视频"
python3 {SKILL_DIR}/generate_video.py --prompt "..." --first-frame-url "https://example.com/frame.png" --duration 5

# With first and last frame: "首帧和尾帧都固定"
python3 {SKILL_DIR}/generate_video.py --prompt "..." --model veo3.1-fast --first-frame-url "https://example.com/start.png" --last-frame-url "https://example.com/end.png"

# Save to a specific folder
python3 {SKILL_DIR}/generate_video.py --prompt "..." --output-dir "apps/starter/public"
```

## Handling Output

The script outputs JSON to stdout.

**On success** (`status: "success"`):

- Display each video URL
- Mention the local saved file path(s) from `saved_paths`

**On error** (`status: "error"`):

- Display the error message to the user
- If the error mentions moderation or safety, suggest the user adjust their prompt

**On config error** (`status: "config_error"`):

- Tell the user which environment variable needs to be set

## Environment Variables Required

- `GRSAI_API_KEY` — required for `grsai`
- `KIE_API_KEY` — required for `kie`
- `GRSAI_OSS_ID` — optional, for `grsai`
- `GRSAI_OSS_PATH` — optional, defaults to `aigc`
- `MOCK_VIDEO_PROVIDER_ENABLED=true` — required to use `mock`

If these values already exist in the shell environment, they take priority over anything loaded from `.env`.
