#!/usr/bin/env python3
import argparse
import asyncio
import json
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

PROMPT_PRESETS = {
    "summary": (
        "请用中文简要总结这个视频的大意。"
        "重点说清它主要在演什么、核心情绪是什么、结局大概是什么。"
        "尽量简洁，不要展开太多细节。不要依赖字幕或配音。"
    ),
    "story-detailed": (
        "请按时间顺序，用中文详细描述这个视频里视觉上演绎的故事。"
        "不要依赖字幕或配音，重点描述画面中发生了什么、人物关系和情绪变化。"
        "如果画面信息不足以确定，就明确说不确定。"
    ),
    "characters": (
        "请用中文分析这个视频中的主要人物和人物关系。"
        "说明谁和谁是什么关系、各自呈现出什么情绪或动机，以及这些关系如何变化。"
        "不要依赖字幕或配音。"
    ),
    "beats": (
        "请用中文分析这个视频的节奏、反转、笑点或关键情节点。"
        "说明它如何铺垫、什么时候转折、最抓人的地方在哪。"
        "不要依赖字幕或配音。"
    ),
}

ALL_MODE_ORDER = ["summary", "story-detailed", "beats", "characters"]

DEFAULT_INPUT_PRICE_PER_M = 0.5
DEFAULT_OUTPUT_PRICE_PER_M = 3.0
DEFAULT_CACHED_INPUT_PRICE_PER_M = 0.0
DEFAULT_CACHE_TTL = "3600s"
CACHE_THRESHOLD_SECONDS = 300
DEFAULT_YTDLP_FORMAT = "bv*[height<=720]+ba/b[height<=720]/b"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def estimate_cost(input_tokens, output_tokens, input_price_per_m, output_price_per_m, cached_tokens=0, cached_input_price_per_m=0.0):
    cached_tokens = cached_tokens or 0
    non_cached_tokens = max((input_tokens or 0) - cached_tokens, 0)
    input_cost = non_cached_tokens / 1_000_000 * input_price_per_m
    cached_input_cost = cached_tokens / 1_000_000 * cached_input_price_per_m
    output_cost = (output_tokens or 0) / 1_000_000 * output_price_per_m
    return input_cost, cached_input_cost, output_cost, input_cost + cached_input_cost + output_cost


def sanitize_stem(value: str):
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return slug or "video"


def is_probably_url(value: str):
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def find_downloaded_video(download_dir: Path):
    candidates = []
    for path in download_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"No video file found in {download_dir}")
    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0]


async def extract_douyin_aweme_detail(url: str):
    try:
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
        from playwright._impl._errors import TargetClosedError
    except ImportError as exc:
        raise RuntimeError("Douyin URL support requires Playwright in the active Python environment.") from exc

    aweme_detail = None
    found_event = asyncio.Event()

    async def intercept_response(response):
        nonlocal aweme_detail
        try:
            if "application/json" not in response.headers.get("content-type", "").lower():
                return
            payload = await response.json()
            if isinstance(payload, dict) and payload.get("aweme_detail"):
                aweme_detail = payload["aweme_detail"]
                found_event.set()
        except Exception:
            return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(java_script_enabled=True)
        page = await context.new_page()
        page.on("response", intercept_response)

        async def navigate():
            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
            except PlaywrightTimeoutError:
                return

        nav_task = asyncio.create_task(navigate())
        try:
            try:
                await asyncio.wait_for(found_event.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
            if not aweme_detail:
                raise RuntimeError("Failed to extract aweme_detail from Douyin page responses.")
            return aweme_detail
        finally:
            if not nav_task.done():
                nav_task.cancel()
                try:
                    await nav_task
                except (asyncio.CancelledError, TargetClosedError):
                    pass
            try:
                await page.close()
            except TargetClosedError:
                pass
            try:
                await context.close()
            except TargetClosedError:
                pass
            try:
                await browser.close()
            except TargetClosedError:
                pass


def download_url_with_headers(url: str, out_path: Path, referer: str):
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0",
            "Referer": referer,
            "Range": "bytes=0-",
            "Accept": "*/*",
            "Origin": "https://www.douyin.com",
        },
    )
    with urlopen(req, timeout=60) as response, open(out_path, "wb") as handle:
        shutil.copyfileobj(response, handle)



def download_douyin_direct(input_value: str):
    download_dir = Path(tempfile.mkdtemp(prefix="gemini-video-douyin-"))
    aweme_detail = asyncio.run(extract_douyin_aweme_detail(input_value))
    video = aweme_detail.get("video") or {}
    candidates = []
    for key in ("play_addr_h264", "play_addr", "download_addr"):
        addr = video.get(key) or {}
        for item in addr.get("url_list") or []:
            if item and item.startswith("http"):
                candidates.append(item.replace("http://", "https://", 1))
    if not candidates:
        raise RuntimeError("No downloadable Douyin video URL found in aweme_detail.")
    video_path = download_dir / "douyin-video.mp4"
    last_error = None
    for candidate in candidates:
        try:
            download_url_with_headers(candidate, video_path, input_value)
            if video_path.exists() and video_path.stat().st_size > 0:
                return video_path, download_dir
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Failed to download Douyin video track: {last_error}")


def resolve_video_input(input_value: str, ytdlp_format: str):
    if is_probably_url(input_value):
        parsed = urlparse(input_value)
        if "douyin.com" in parsed.netloc:
            video_path, download_dir = download_douyin_direct(input_value)
            return video_path, download_dir, {"type": "url", "source": input_value, "downloader": "embedded-playwright"}
        yt_dlp = shutil.which("yt-dlp")
        if not yt_dlp:
            raise RuntimeError("yt-dlp is required for URL input but was not found in PATH.")
        download_dir = Path(tempfile.mkdtemp(prefix="gemini-video-download-"))
        base_name = sanitize_stem(Path(parsed.path).stem or parsed.netloc)
        output_template = str(download_dir / f"{base_name}.%(ext)s")
        cmd = [
            yt_dlp,
            "--no-playlist",
            "--format",
            ytdlp_format,
            "--merge-output-format",
            "mp4",
            "--restrict-filenames",
            "-o",
            output_template,
            input_value,
        ]
        print(f"[download] {' '.join(cmd)}", file=sys.stderr)
        subprocess.run(cmd, check=True)
        return find_downloaded_video(download_dir), download_dir, {"type": "url", "source": input_value, "downloader": "yt-dlp"}

    video_path = Path(input_value).expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")
    return video_path, None, {"type": "file", "source": str(video_path)}


def get_video_duration_seconds(video_path: Path):
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            try:
                return float(res.stdout.strip())
            except ValueError:
                pass
    if platform.system() == "Darwin":
        cmd = ["mdls", "-raw", "-name", "kMDItemDurationSeconds", str(video_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip() not in {"(null)", ""}:
            try:
                return float(res.stdout.strip())
            except ValueError:
                pass
    return None


def wait_until_active(client, file_name: str, timeout_s: int = 180, interval_s: int = 3):
    deadline = time.time() + timeout_s
    last_state = None
    while time.time() < deadline:
        f = client.files.get(name=file_name)
        state = getattr(getattr(f, "state", None), "name", None) or str(getattr(f, "state", "UNKNOWN"))
        last_state = state
        print(f"[wait] file state = {state}", file=sys.stderr)
        if state == "ACTIVE":
            return f
        if state == "FAILED":
            raise RuntimeError(f"File processing failed: {f}")
        time.sleep(interval_s)
    raise TimeoutError(f"Timed out waiting for file to become ACTIVE. Last state: {last_state}")


def main():
    parser = argparse.ArgumentParser(description="Download or upload a video, then analyze it with Gemini.")
    parser.add_argument("video", help="Local video path or supported video URL")
    parser.add_argument("--model", default="gemini-3-flash-preview", help="Gemini model name")
    parser.add_argument("--mode", choices=sorted(list(PROMPT_PRESETS.keys()) + ["all"]), default="summary", help="Preset analysis mode")
    parser.add_argument("--prompt", help="Custom prompt. Overrides --mode")
    parser.add_argument("--timeout", type=int, default=180, help="Seconds to wait for uploaded file to become ACTIVE")
    parser.add_argument("--input-price-per-m", type=float, default=DEFAULT_INPUT_PRICE_PER_M, help="Input price per 1M tokens")
    parser.add_argument("--cached-input-price-per-m", type=float, default=DEFAULT_CACHED_INPUT_PRICE_PER_M, help="Cached input price per 1M tokens")
    parser.add_argument("--output-price-per-m", type=float, default=DEFAULT_OUTPUT_PRICE_PER_M, help="Output price per 1M tokens")
    parser.add_argument("--cache-ttl", default=DEFAULT_CACHE_TTL, help="Explicit cache TTL, e.g. 3600s")
    parser.add_argument("--cache-over-minutes", type=float, default=5.0, help="Enable explicit cache when video duration exceeds this many minutes")
    parser.add_argument("--ytdlp-format", default=DEFAULT_YTDLP_FORMAT, help="yt-dlp format selector for URL downloads")
    parser.add_argument("--json", action="store_true", help="Print response text plus usage/cost as JSON")
    args = parser.parse_args()

    video_path, temp_download_dir, source_info = resolve_video_input(args.video, args.ytdlp_format)

    prompt = args.prompt or (PROMPT_PRESETS[args.mode] if args.mode != "all" else None)

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise RuntimeError("Missing dependency: google-genai. Install it in the chosen Python environment.")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Export it first or source ~/.profile.")

    _mime_type, _ = mimetypes.guess_type(str(video_path))
    client = genai.Client(api_key=api_key)

    def response_to_record(label, response):
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", None)
        candidates_tokens = getattr(usage, "candidates_token_count", None)
        total_tokens = getattr(usage, "total_token_count", None)
        thoughts_tokens = getattr(usage, "thoughts_token_count", None)
        cached_tokens = getattr(usage, "cached_content_token_count", None)
        input_cost, cached_input_cost, output_cost, total_cost = estimate_cost(
            prompt_tokens,
            candidates_tokens,
            args.input_price_per_m,
            args.output_price_per_m,
            cached_tokens=cached_tokens,
            cached_input_price_per_m=args.cached_input_price_per_m,
        )
        return {
            "label": label,
            "text": getattr(response, "text", None),
            "usage": {
                "prompt_token_count": prompt_tokens,
                "candidates_token_count": candidates_tokens,
                "thoughts_token_count": thoughts_tokens,
                "cached_content_token_count": cached_tokens,
                "total_token_count": total_tokens,
            },
            "cost": {
                "input_price_per_m": args.input_price_per_m,
                "cached_input_price_per_m": args.cached_input_price_per_m,
                "output_price_per_m": args.output_price_per_m,
                "input_cost": input_cost,
                "cached_input_cost": cached_input_cost,
                "output_cost": output_cost,
                "total_cost": total_cost,
            },
        }

    upload_path = video_path
    cleanup_dir = None
    try:
        try:
            str(video_path).encode("ascii")
        except UnicodeEncodeError:
            cleanup_dir = tempfile.mkdtemp(prefix="gemini-video-")
            upload_path = Path(cleanup_dir) / f"upload{video_path.suffix.lower() or '.mp4'}"
            shutil.copy2(video_path, upload_path)

        print(f"[upload] {upload_path}", file=sys.stderr)
        uploaded = client.files.upload(file=str(upload_path))
        file_name = uploaded.name
        print(f"[upload] name={file_name} uri={getattr(uploaded, 'uri', '')}", file=sys.stderr)

        active_file = wait_until_active(client, file_name=file_name, timeout_s=args.timeout)

        duration_s = get_video_duration_seconds(video_path)
        use_explicit_cache = duration_s is not None and duration_s > args.cache_over_minutes * 60
        cache = None
        generate_config = None
        if use_explicit_cache:
            print(f"[cache] enabling explicit cache for duration={duration_s:.3f}s", file=sys.stderr)
            cache = client.caches.create(
                model=args.model,
                config=types.CreateCachedContentConfig(
                    display_name=video_path.stem[:64],
                    system_instruction=(
                        "You are an expert video analyzer. Base answers on the uploaded video. "
                        "Call out uncertainty when dialogue would be required."
                    ),
                    contents=[active_file],
                    ttl=args.cache_ttl,
                ),
            )
            generate_config = types.GenerateContentConfig(cached_content=cache.name)
            print(f"[cache] name={cache.name}", file=sys.stderr)

        def run_once(label, this_prompt):
            if generate_config is not None:
                response = client.models.generate_content(
                    model=args.model,
                    contents=this_prompt,
                    config=generate_config,
                )
            else:
                response = client.models.generate_content(
                    model=args.model,
                    contents=[this_prompt, active_file],
                )
            return response_to_record(label, response)

        if args.mode == "all":
            analyses = {}
            for label in ALL_MODE_ORDER:
                analyses[label] = run_once(label, PROMPT_PRESETS[label])
            synthesis_prompt = (
                "请基于以下四份分析结果，输出一份中文综合结论。先给整体大意，再概括剧情主线，"
                "然后总结节奏/笑点/反转，最后点出人物关系变化。若各分析之间有冲突，请指出。\n\n"
                + "\n\n".join(f"[{label}]\n{analyses[label]['text'] or ''}" for label in ALL_MODE_ORDER)
            )
            synthesis = run_once("synthesis", synthesis_prompt)
            records = list(analyses.values()) + [synthesis]
            total_prompt = sum((r["usage"]["prompt_token_count"] or 0) for r in records)
            total_output = sum((r["usage"]["candidates_token_count"] or 0) for r in records)
            total_thoughts = sum((r["usage"]["thoughts_token_count"] or 0) for r in records)
            total_cached = sum((r["usage"]["cached_content_token_count"] or 0) for r in records)
            total_tokens = sum((r["usage"]["total_token_count"] or 0) for r in records)
            total_cost = sum(r["cost"]["total_cost"] for r in records)
            result = {
                "model": args.model,
                "mode": args.mode,
                "video": str(video_path),
                "source": source_info,
                "duration_seconds": duration_s,
                "used_explicit_cache": bool(cache),
                "cache_name": getattr(cache, "name", None),
                "analyses": analyses,
                "synthesis": synthesis["text"],
                "usage": {
                    "prompt_token_count": total_prompt,
                    "candidates_token_count": total_output,
                    "thoughts_token_count": total_thoughts,
                    "cached_content_token_count": total_cached,
                    "total_token_count": total_tokens,
                },
                "cost": {
                    "total_cost": total_cost,
                },
            }
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
            for label in ALL_MODE_ORDER:
                print(f"\n===== {label} =====\n{analyses[label]['text'] or ''}")
            print(f"\n===== synthesis =====\n{synthesis['text'] or ''}")
            print(
                f"\n\n——\n本次消耗：prompt {total_prompt} / output {total_output} / total {total_tokens} tokens"
                f"；缓存命中：{total_cached}；估算费用：${total_cost:.6f}"
            )
            return

        record = run_once(args.mode, prompt)
        result = {
            "model": args.model,
            "mode": args.mode,
            "video": str(video_path),
            "source": source_info,
            "duration_seconds": duration_s,
            "used_explicit_cache": bool(cache),
            "cache_name": getattr(cache, "name", None),
            "text": record["text"],
            "usage": record["usage"],
            "cost": record["cost"],
        }

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        if record["text"]:
            print(record["text"])
        else:
            print(result)

        tail = (
            f"\n\n——\n本次消耗：prompt {record['usage']['prompt_token_count']} / output {record['usage']['candidates_token_count']} / total {record['usage']['total_token_count']} tokens"
            f"；缓存命中：{record['usage']['cached_content_token_count'] or 0}"
            f"；估算费用：${record['cost']['total_cost']:.6f}"
        )
        print(tail)
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
        if temp_download_dir:
            shutil.rmtree(temp_download_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
