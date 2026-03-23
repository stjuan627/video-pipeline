#!/usr/bin/env python3
"""Image generation script for Claude Code skill.

Generates images via grsai and kie API providers.
Outputs JSON to stdout, logs progress to stderr.
Uses only Python stdlib — no external dependencies.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import mimetypes
import os
import re
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_INTERVAL = 10  # seconds between polling attempts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    """Log a message to stderr."""
    print(f"[image-gen] {msg}", file=sys.stderr)


def json_output(data: dict) -> None:
    """Write JSON result to stdout."""
    print(json.dumps(data, ensure_ascii=False))


def _safe_slug(value: str, max_length: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        return "image"
    return slug[:max_length].rstrip("-") or "image"


def _guess_extension(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        return ext
    return ".png"


def _parse_env_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {'"', "'"} and value[-1:] == value[0]:
        return value[1:-1]
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def _load_dotenv_file(file_path: str) -> bool:
    loaded = False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw_value = line.split("=", 1)
                key = key.strip()
                if not key or key.startswith("#") or key in os.environ:
                    continue
                os.environ[key] = _parse_env_value(raw_value)
                loaded = True
    except OSError:
        return False
    return loaded


def load_dotenv_from_cwd() -> list[str]:
    loaded_files: list[str] = []
    current = os.path.abspath(os.getcwd())
    while True:
        env_path = os.path.join(current, ".env")
        if os.path.isfile(env_path) and _load_dotenv_file(env_path):
            loaded_files.append(env_path)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return loaded_files


def _aws_sigv4_headers(
    method: str, url: str, headers: dict, payload: bytes,
    access_key: str, secret_key: str, region: str = "auto", service: str = "s3",
) -> dict:
    """Generate AWS Signature V4 headers for an HTTP request."""
    import hashlib, hmac
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    path = urllib.parse.quote(parsed.path, safe="/")
    now = dt.datetime.now(dt.timezone.utc)
    datestamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    payload_hash = hashlib.sha256(payload).hexdigest()

    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = f"{method}\n{path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    credential_scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = _sign(f"AWS4{secret_key}".encode(), datestamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth = f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return {
        **headers,
        "Authorization": auth,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Host": host,
    }


def _upload_to_r2(data: bytes, mime: str, filename: str) -> str | None:
    """Upload bytes to Cloudflare R2 via S3-compatible API and return the public URL."""
    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    bucket = os.environ.get("R2_BUCKET") or os.environ.get("R2_BUCKET_NAME")
    public_url = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    if not all([account_id, access_key, secret_key, bucket, public_url]):
        return None

    import hashlib
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    content_hash = hashlib.md5(data).hexdigest()[:8]
    ext = mimetypes.guess_extension(mime) or ".bin"
    if ext == ".jpe":
        ext = ".jpg"
    object_key = f"image-gen/{ts}-{content_hash}{ext}"

    s3_url = f"https://{account_id}.r2.cloudflarestorage.com/{bucket}/{object_key}"
    headers = _aws_sigv4_headers(
        "PUT", s3_url, {"Content-Type": mime}, data,
        access_key, secret_key,
    )
    req = urllib.request.Request(s3_url, data=data, method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
        public = f"{public_url}/{object_key}"
        log(f"uploaded to R2: {public} ({len(data)//1024}KB)")
        return public
    except Exception as e:
        log(f"R2 upload failed: {e}")
        return None


def _compress_image_bytes(abs_path: str, max_bytes: int = 4 * 1024 * 1024) -> tuple[bytes, str]:
    """Compress an image to fit within max_bytes. Returns (data, mime)."""
    with open(abs_path, "rb") as f:
        raw = f.read()
    if len(raw) <= max_bytes:
        mime, _ = mimetypes.guess_type(abs_path)
        return raw, mime or "image/png"
    try:
        from PIL import Image
        import io
        img = Image.open(abs_path)
        w, h = img.size
        while w * h > 4_000_000:
            w, h = w * 3 // 4, h * 3 // 4
        if (w, h) != img.size:
            img = img.resize((w, h), Image.LANCZOS)
            log(f"resized to {w}x{h}")
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        for quality in (90, 80, 70, 50):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                log(f"compressed to JPEG q={quality} ({len(data)//1024}KB)")
                return data, "image/jpeg"
        return data, "image/jpeg"
    except ImportError:
        log("PIL not available, sending raw image")
        mime, _ = mimetypes.guess_type(abs_path)
        return raw, mime or "image/png"


def resolve_image_url(path_or_url: str) -> str:
    """Upload a local file to R2 and return the public URL. Pass through remote URLs."""
    if path_or_url.startswith(("http://", "https://", "data:")):
        return path_or_url
    abs_path = os.path.abspath(path_or_url)
    if not os.path.isfile(abs_path):
        log(f"warning: local file not found, passing as-is: {path_or_url}")
        return path_or_url
    data, mime = _compress_image_bytes(abs_path)
    r2_url = _upload_to_r2(data, mime, os.path.basename(abs_path))
    if r2_url:
        return r2_url
    # Fallback to base64
    b64 = base64.b64encode(data).decode("ascii")
    log(f"fallback to base64 data URL: {os.path.basename(abs_path)} ({len(b64)//1024}KB)")
    return f"data:{mime};base64,{b64}"


def resolve_image_urls(paths: list[str] | None) -> list[str] | None:
    """Resolve a list of image paths/URLs."""
    if not paths:
        return paths
    return [resolve_image_url(p) for p in paths]


def download_image(url: str, prompt: str, index: int, output_dir: str) -> str:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{_safe_slug(prompt)}-{index:02d}{_guess_extension(url)}"
    local_path = os.path.join(output_dir, filename)

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with (
            urllib.request.urlopen(req, timeout=120) as resp,
            open(local_path, "wb") as f,
        ):
            f.write(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"failed to download image {index} from {url}: HTTP {exc.code} {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"failed to download image {index} from {url}: {exc.reason}"
        ) from exc

    return local_path


def download_images(urls: list[str], prompt: str, output_dir: str) -> list[str]:
    os.makedirs(output_dir, exist_ok=True)
    saved_paths: list[str] = []
    for index, url in enumerate(urls, start=1):
        saved_path = download_image(url, prompt, index, output_dir)
        log(f"[{index}] saved locally — {saved_path}")
        saved_paths.append(saved_path)
    return saved_paths


def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    body: dict | None = None,
    timeout: int = 60,
) -> dict:
    """Perform an HTTP request and return parsed JSON response."""
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
            err_detail = json.loads(err_body)
        except (json.JSONDecodeError, Exception):
            err_detail = err_body
        raise RuntimeError(
            f"HTTP {exc.code} {exc.reason} for {method} {url}: {err_detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL error for {method} {url}: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# Status normalisation
# ---------------------------------------------------------------------------

_STATUS_MAP_GRSAI: dict[str, str] = {
    "queued": "queued",
    "pending": "queued",
    "waiting": "queued",
    "processing": "processing",
    "running": "processing",
    "in_progress": "processing",
    "in-progress": "processing",
    "succeeded": "succeeded",
    "success": "succeeded",
    "completed": "succeeded",
    "failed": "failed",
    "error": "failed",
    "canceled": "failed",
    "cancelled": "failed",
}

_STATUS_MAP_KIE: dict[str, str] = {
    "waiting": "queued",
    "queuing": "queued",
    "generating": "processing",
    "success": "succeeded",
    "fail": "failed",
}


def normalise_status(raw: str, provider: str) -> str:
    """Normalise a raw status string into queued/processing/succeeded/failed."""
    raw_lower = raw.lower()
    mapping = _STATUS_MAP_KIE if provider == "kie" else _STATUS_MAP_GRSAI
    return mapping.get(raw_lower, raw_lower)


# ---------------------------------------------------------------------------
# GRSAI provider
# ---------------------------------------------------------------------------

_GRSAI_BASE = "https://api.grsai.com/v1/draw"

# gpt-1.5-image only accepts these aspect ratios
_GPT_SIZE_MAP: dict[str, str] = {
    "1:1": "1:1",
    "2:3": "2:3",
    "3:2": "3:2",
    "9:16": "2:3",
    "16:9": "3:2",
}


def _grsai_headers() -> dict[str, str]:
    key = os.environ["GRSAI_API_KEY"]
    headers: dict[str, str] = {"Authorization": f"Bearer {key}"}
    oss_id = os.environ.get("GRSAI_OSS_ID")
    if oss_id:
        headers["oss-id"] = oss_id
        oss_path = os.environ.get("GRSAI_OSS_PATH", "aigc")
        headers["oss-path"] = oss_path
    return headers


def _grsai_create_body(
    model: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    source_images: list[str] | None,
) -> tuple[str, dict]:
    """Return (endpoint_subpath, request_body) for grsai create-job."""
    if model == "gpt-1.5-image":
        size = _GPT_SIZE_MAP.get(aspect_ratio, "auto")
        body: dict = {
            "model": "sora-image",
            "prompt": prompt,
            "size": size,
            "variants": 1,
            "webHook": "-1",
            "shutProgress": True,
        }
        if source_images:
            body["urls"] = resolve_image_urls(source_images)
        return "completions", body

    # nano-banana family → /v1/draw/nano-banana
    body = {
        "model": model,
        "prompt": prompt,
        "webHook": "-1",
        "shutProgress": True,
    }
    if aspect_ratio:
        body["aspectRatio"] = aspect_ratio
    if source_images:
        body["urls"] = resolve_image_urls(source_images)

    # nano-banana-pro and nano-banana-2 accept imageSize; fast does not
    if model in ("nano-banana-pro", "nano-banana-2"):
        body["imageSize"] = resolution

    return "nano-banana", body


def _extract_job_id(data: dict) -> str:
    """Extract job ID from grsai create-job response, trying multiple paths."""
    nested = data.get("data", {})
    if isinstance(nested, dict):
        for key in ("id", "jobId", "job_id", "task_id"):
            val = nested.get(key)
            if val:
                return str(val)
    for key in ("id", "jobId"):
        val = data.get(key)
        if val:
            return str(val)
    raise ValueError(f"Cannot extract job ID from response: {json.dumps(data)}")


def grsai_create_job(
    model: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    source_images: list[str] | None,
) -> str:
    """Create a generation job on grsai. Returns a job ID."""
    endpoint, body = _grsai_create_body(
        model, prompt, aspect_ratio, resolution, source_images
    )
    url = f"{_GRSAI_BASE}/{endpoint}"
    headers = _grsai_headers()

    log(
        f"grsai: creating job at {_GRSAI_BASE}/{endpoint} with model={body.get('model', model)}"
    )
    resp = http_request(url, method="POST", headers=headers, body=body)

    if resp.get("code", 0) != 0:
        raise RuntimeError(f"grsai create-job error: {resp.get('message', resp)}")

    job_id = _extract_job_id(resp)
    log(f"grsai: job created — id={job_id}")
    return job_id


def grsai_poll_result(job_id: str, timeout: int) -> str:
    """Poll grsai for a job result. Returns the image URL."""
    url = f"{_GRSAI_BASE}/result"
    headers = _grsai_headers()
    deadline = time.monotonic() + timeout

    while True:
        time.sleep(POLL_INTERVAL)

        if time.monotonic() >= deadline:
            raise TimeoutError(f"grsai: job {job_id} timed out after {timeout}s")

        resp = http_request(url, method="POST", headers=headers, body={"id": job_id})

        if resp.get("code", 0) != 0:
            raise RuntimeError(f"grsai poll error: {resp.get('message', resp)}")

        data = resp.get("data", {})
        raw_status = data.get("status", "")
        status = normalise_status(raw_status, "grsai")
        log(f"grsai: job {job_id} status={status} (raw={raw_status})")

        if status == "failed":
            reason = data.get("failure_reason", "")
            if reason in ("input_moderation", "output_moderation"):
                raise RuntimeError(f"grsai: content moderation error ({reason})")
            raise RuntimeError(f"grsai: job {job_id} failed — {reason or data}")

        if status == "succeeded":
            # Try multiple result paths
            results = data.get("results")
            if isinstance(results, list) and results:
                img_url = results[0].get("url", "")
                if img_url:
                    return img_url
            for key in ("result", "url"):
                val = data.get(key)
                if val and isinstance(val, str):
                    return val
            raise RuntimeError(
                f"grsai: succeeded but no image URL found in {json.dumps(data)}"
            )


# ---------------------------------------------------------------------------
# KIE provider
# ---------------------------------------------------------------------------

_KIE_BASE = "https://api.kie.ai/api/v1/jobs"


def _kie_headers() -> dict[str, str]:
    key = os.environ["KIE_API_KEY"]
    return {"Authorization": f"Bearer {key}"}


def kie_create_job(
    model: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    source_images: list[str] | None,
) -> str:
    """Create a generation job on kie. Returns a task ID."""
    url = f"{_KIE_BASE}/createTask"
    headers = _kie_headers()

    input_data: dict = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    if source_images:
        input_data["image_input"] = resolve_image_urls(source_images)

    body = {"model": model, "input": input_data}

    log(f"kie: creating job with model={model}")
    resp = http_request(url, method="POST", headers=headers, body=body)

    if resp.get("code") != 200:
        raise RuntimeError(f"kie create-job error: {resp.get('message', resp)}")

    task_id = resp.get("data", {}).get("taskId")
    if not task_id:
        raise RuntimeError(f"kie: no taskId in response: {json.dumps(resp)}")

    log(f"kie: job created — taskId={task_id}")
    return str(task_id)


def kie_poll_result(job_id: str, timeout: int) -> str:
    """Poll kie for a job result. Returns the image URL."""
    headers = _kie_headers()
    deadline = time.monotonic() + timeout

    while True:
        time.sleep(POLL_INTERVAL)

        if time.monotonic() >= deadline:
            raise TimeoutError(f"kie: job {job_id} timed out after {timeout}s")

        url = f"{_KIE_BASE}/recordInfo?taskId={job_id}"
        resp = http_request(url, method="GET", headers=headers)

        if resp.get("code") != 200:
            raise RuntimeError(f"kie poll error: {resp.get('message', resp)}")

        data = resp.get("data", {})
        raw_status = data.get("state", "")
        status = normalise_status(raw_status, "kie")
        log(f"kie: job {job_id} status={status} (raw={raw_status})")

        if status == "failed":
            raise RuntimeError(f"kie: job {job_id} failed — {data}")

        if status == "succeeded":
            result_json_str = data.get("resultJson", "")
            if not result_json_str:
                raise RuntimeError(
                    f"kie: succeeded but no resultJson in {json.dumps(data)}"
                )
            try:
                result_obj = json.loads(result_json_str)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"kie: failed to parse resultJson: {exc}") from exc

            result_urls = result_obj.get("resultUrls", [])
            if not result_urls:
                raise RuntimeError(
                    f"kie: no resultUrls in resultJson: {result_json_str}"
                )
            return result_urls[0]


# ---------------------------------------------------------------------------
# Unified generation
# ---------------------------------------------------------------------------


def generate_single(
    provider: str,
    model: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    source_images: list[str] | None,
    timeout: int,
    index: int,
) -> str:
    """Generate a single image. Returns the image URL."""
    log(f"[{index}] starting generation (provider={provider}, model={model})")

    if provider == "grsai":
        job_id = grsai_create_job(
            model, prompt, aspect_ratio, resolution, source_images
        )
        img_url = grsai_poll_result(job_id, timeout)
    else:
        job_id = kie_create_job(model, prompt, aspect_ratio, resolution, source_images)
        img_url = kie_poll_result(job_id, timeout)

    log(f"[{index}] done — {img_url}")
    return img_url


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_KIE_SUPPORTED_MODELS = {"nano-banana-pro", "nano-banana-2"}
_ALL_MODELS = {"nano-banana-fast", "nano-banana-pro", "nano-banana-2", "gpt-1.5-image"}


def validate(args: argparse.Namespace) -> str | None:
    """Validate configuration. Returns an error message or None."""
    # Provider env vars
    if args.provider == "grsai" and not os.environ.get("GRSAI_API_KEY"):
        return "GRSAI_API_KEY environment variable is required for the grsai provider"
    if args.provider == "kie" and not os.environ.get("KIE_API_KEY"):
        return "KIE_API_KEY environment variable is required for the kie provider"

    # Model recognition
    if args.model not in _ALL_MODELS:
        return (
            f"Unrecognized model: {args.model}. "
            f"Supported models: {', '.join(sorted(_ALL_MODELS))}"
        )

    # Model compatibility
    if args.provider == "kie" and args.model not in _KIE_SUPPORTED_MODELS:
        return (
            f"kie provider only supports models: {', '.join(sorted(_KIE_SUPPORTED_MODELS))}. "
            f"Got: {args.model}"
        )

    # Count range
    if not 1 <= args.count <= 4:
        return "count must be between 1 and 4"

    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate images via grsai / kie API providers."
    )
    parser.add_argument("--prompt", required=True, help="Image generation prompt")
    parser.add_argument(
        "--model",
        default="nano-banana-fast",
        help="Model ID (default: nano-banana-fast)",
    )
    parser.add_argument(
        "--provider",
        default="grsai",
        choices=["grsai", "kie"],
        help="API provider (default: grsai)",
    )
    parser.add_argument(
        "--aspect-ratio", default="9:16", help="Aspect ratio (default: 9:16)"
    )
    parser.add_argument(
        "--resolution",
        default="2K",
        choices=["1K", "2K", "4K"],
        help="Resolution for nano-banana-pro/2 (default: 2K)",
    )
    parser.add_argument(
        "--source-images", nargs="*", default=None, help="Reference image URLs"
    )
    parser.add_argument(
        "--count", type=int, default=1, help="Number of images 1-4 (default: 1)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout per image in seconds (default: 300)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for downloaded images (default: current working directory)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    loaded_env_files = load_dotenv_from_cwd()
    for env_path in loaded_env_files:
        log(f"loaded env - {env_path}")

    # Validate configuration
    error = validate(args)
    if error:
        json_output({"status": "config_error", "message": error})
        sys.exit(2)

    provider = args.provider
    model = args.model
    output_dir = os.path.abspath(args.output_dir)

    # Single image — simple path
    if args.count == 1:
        try:
            url = generate_single(
                provider,
                model,
                args.prompt,
                args.aspect_ratio,
                args.resolution,
                args.source_images,
                args.timeout,
                1,
            )
            saved_paths = download_images([url], args.prompt, output_dir)
            json_output(
                {
                    "status": "success",
                    "urls": [url],
                    "saved_paths": saved_paths,
                    "output_dir": output_dir,
                    "model": model,
                    "provider": provider,
                }
            )
            sys.exit(0)
        except Exception as exc:
            log(f"error: {exc}")
            json_output(
                {
                    "status": "error",
                    "message": str(exc),
                    "model": model,
                    "provider": provider,
                }
            )
            sys.exit(1)

    # Multi-image — parallel via ThreadPoolExecutor
    urls: list[str] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=min(args.count, 4)) as pool:
        futures = {
            pool.submit(
                generate_single,
                provider,
                model,
                args.prompt,
                args.aspect_ratio,
                args.resolution,
                args.source_images,
                args.timeout,
                i + 1,
            ): i
            for i in range(args.count)
        }

        for future in as_completed(futures):
            idx = futures[future]
            try:
                url = future.result()
                urls.append(url)
            except Exception as exc:
                log(f"[{idx + 1}] failed: {exc}")
                errors.append(str(exc))

    if not errors:
        try:
            saved_paths = download_images(urls, args.prompt, output_dir)
        except Exception as exc:
            log(f"download error: {exc}")
            json_output(
                {
                    "status": "error",
                    "message": str(exc),
                    "urls": urls,
                    "model": model,
                    "provider": provider,
                }
            )
            sys.exit(1)
        json_output(
            {
                "status": "success",
                "urls": urls,
                "saved_paths": saved_paths,
                "output_dir": output_dir,
                "model": model,
                "provider": provider,
            }
        )
        sys.exit(0)
    elif urls:
        saved_paths: list[str] = []
        save_error: str | None = None
        try:
            saved_paths = download_images(urls, args.prompt, output_dir)
        except Exception as exc:
            log(f"partial download error: {exc}")
            save_error = str(exc)
            errors.append(save_error)
        json_output(
            {
                "status": "partial",
                "urls": urls,
                "saved_paths": saved_paths,
                "output_dir": output_dir,
                "errors": errors,
                "model": model,
                "provider": provider,
            }
        )
        sys.exit(1)
    else:
        json_output(
            {
                "status": "error",
                "message": "; ".join(errors),
                "model": model,
                "provider": provider,
            }
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
