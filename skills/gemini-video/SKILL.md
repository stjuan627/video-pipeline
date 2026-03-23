---
name: gemini-video
description: Analyze videos with the Gemini Developer API by accepting either a local video file or a supported video URL. Douyin links should use an embedded Playwright-based extractor that captures aweme_detail and downloads the real video track directly; other mainstream platforms should auto-download with yt-dlp at no more than 720p before Gemini analysis. Use when the user asks to watch, understand, summarize, explain, or describe a video, especially requests like "这个视频讲什么", "看看这个视频", "用 Gemini 分析这个视频", or when the source is a Douyin/YouTube/Bilibili/X/Twitter/Reddit and similar mainstream video link.
---

# Gemini Video

Use this skill for true video understanding through the Gemini API.

## Workflow

1. Treat the task as video understanding first, not web-page summarization.
2. Accept either a local file path or a supported video URL.
3. If the input is a Douyin URL, use the embedded Playwright extractor to capture `aweme_detail` and download the real video track directly.
4. If the input is another supported video URL, use `yt-dlp` to download a local copy before analysis.
5. Keep the default `yt-dlp` download ceiling at 720p to control size, speed, and token cost.
6. Upload the local video to Gemini, wait until the file becomes `ACTIVE`, then call the model.
7. If the video is longer than 5 minutes, create an explicit cache and reuse it for subsequent requests in the same run.
8. Prefer `--json` when the user cares about token usage, cost, or structured output.

## Input routing

- `这个视频讲什么` / `看下这个视频` / `总结一下这个视频` -> route here even when the user only provides a social-media link.
- Douyin links -> embedded Playwright extraction first, because raw `yt-dlp` is more likely to fail on current Douyin anti-bot checks.
- YouTube/Bilibili/X/Twitter/Reddit and similar mainstream video links -> `yt-dlp` first.
- Local `mp4` / `mov` / `mkv` / `webm` and similar files -> upload directly.
- Only fall back to webpage summarization if the video cannot be downloaded or the source is clearly not a playable video.

## Request mapping

Use these defaults unless the user gives a custom prompt:

- `看下这个视频的大意` / `总结一下` / `讲讲这视频说什么` -> `summary`
- `剧情详细分析` / `详细讲讲剧情` / `按时间顺序分析` -> `story-detailed`
- `分析人物关系` / `人物关系怎么看` -> `characters`
- `分析笑点/反转/节奏` -> `beats`
- `都分析一下` / `全套分析` / `all` -> `all`

The bundled script supports `--mode` for these presets.

## Command

```bash
source ~/.profile >/dev/null 2>&1 || true
GEMINI_API_KEY="$GEMINI_API_KEY" /tmp/openclaw/google-genai-venv/bin/python \
  /Users/james/.openclaw/workspace/skills/gemini-video/scripts/gemini_video_analyze.py \
  "https://example.com/video" --model gemini-3-flash-preview --mode summary --json
```

For a local file:

```bash
source ~/.profile >/dev/null 2>&1 || true
GEMINI_API_KEY="$GEMINI_API_KEY" /tmp/openclaw/google-genai-venv/bin/python \
  /Users/james/.openclaw/workspace/skills/gemini-video/scripts/gemini_video_analyze.py \
  "/path/to/video.mp4" --model gemini-3-flash-preview --mode summary --json
```

## Downloader behavior

- Douyin URL input prefers the embedded Playwright extractor built into the script.
- The embedded Douyin path captures `aweme_detail`, then downloads the real video track with browser-like headers.
- Other URL input requires `yt-dlp` in `PATH`.
- Default `yt-dlp` selector is `bv*[height<=720]+ba/b[height<=720]/b`.
- Keep the default ceiling at 720p unless the user explicitly asks for higher fidelity.
- Use `--no-playlist` and download one video per request.
- Save to an ASCII-safe path when Gemini upload needs it.

## Notes

- Use the bundled Python script instead of hand-writing curl calls.
- The script automatically handles non-ASCII filenames by copying to a temporary ASCII path before upload.
- The script prints API usage metadata and estimated cost. Default pricing is `$0.5/M` input and `$3/M` output, override with flags if pricing changes.
- If the API returns quota errors, surface them plainly instead of retrying in a tight loop.
- If the file stays in `PROCESSING`, wait; do not call `generate_content` until the file is `ACTIVE`.
- For videos longer than 5 minutes, the script should create an explicit cache and use `cached_content` for repeated analyses in the same run.
- `all` mode should run four separate fresh requests (`summary`, `story-detailed`, `beats`, `characters`) against the same cached video, then do one synthesis pass that consolidates the results.

## Known pitfalls

- `Gemini CLI` is not the right tool for this workflow. It may say it cannot watch videos even when the API can handle local video files.
- Douyin is especially hostile to raw `yt-dlp`; do not make it the first attempt there when the embedded Playwright path is available.
- Social-platform links are fine only if the download stage succeeds; if download fails, say that clearly instead of pretending a webpage summary is video understanding.
- Large files take time to upload and process. A 200MB+ video can sit in `PROCESSING` for a while; do not mistake that for failure.
- Reuse an already-`ACTIVE` file when running multiple prompts or models on the same video. Do not re-upload unless needed.
- Free-tier and preview models can hit quota or transient server errors. A failed generate call does not imply upload failure; isolate the stage before retrying.
- `prompt_token_count` for video requests can be much larger than the handwritten prompt because it includes media tokenization. Use API `usage_metadata`, not rough guesses, for final accounting.
- The API may report `thoughts_token_count`. Do not bill it unless the active pricing model explicitly says to.

## Output expectations

- For `summary`, keep the body concise and give the high-level meaning first.
- For `story-detailed`, describe the plot in time order with more scene detail.
- For `beats`, focus on pacing, reversals, hooks, and joke mechanics.
- For `characters`, focus on relationships, motivations, and changes over time.
- For `all`, present the four sub-analyses plus one consolidated synthesis block.
- For debugging asks, include the raw JSON block from `--json` or quote the important fields.
- If the user wants subtitles/transcript behavior excluded, keep that instruction in the prompt.
- Always end with a short tail reporting `prompt/output/total` tokens, cached token hits when available, and the estimated cost for this run.
