#!/usr/bin/env python3

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
import urllib.error
import urllib.parse
import urllib.request

POLL_INTERVAL = 10

GRSAI_CREATE_ENDPOINTS = {
    "sora-2": "/v1/video/sora-video",
    "veo3.1-fast": "/v1/video/veo",
    "veo3.1-pro": "/v1/video/veo",
}

KIE_MODEL_MAPPING = {
    "veo3.1-fast": "veo3_fast",
    "sora-2": "sora-2",
    "hailuo-2.3": "hailuo/2-3",
    "hailuo-2.3-fast": "hailuo/2-3",
    "kling-2.6": "kling-2.6",
    "grok-imagine": "grok-imagine",
    "seedance-1.0-pro": "bytedance/v1-pro",
    "seedance-1.0-pro-fast": "bytedance/v1-pro-fast",
}

PROVIDER_MODELS = {
    "grsai": {"veo3.1-fast", "veo3.1-pro", "sora-2"},
    "kie": {
        "veo3.1-fast",
        "sora-2",
        "hailuo-2.3",
        "hailuo-2.3-fast",
        "kling-2.6",
        "grok-imagine",
        "seedance-1.0-pro",
        "seedance-1.0-pro-fast",
    },
    "mock": {"mock-video-lab"},
}

STATUS_MAP_GRSAI = {
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

STATUS_MAP_KIE = {
    "waiting": "queued",
    "queuing": "queued",
    "generating": "processing",
    "success": "succeeded",
    "fail": "failed",
}


def log(message: str) -> None:
    print(f"[video-gen] {message}", file=sys.stderr)


def json_output(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False))


def safe_slug(value: str, max_length: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        return "video"
    return slug[:max_length].rstrip("-") or "video"


def guess_extension(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    if ext in {".mp4", ".mov", ".webm", ".m4v"}:
        return ext
    return ".mp4"


def parse_env_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {'"', "'"} and value[-1:] == value[0]:
        return value[1:-1]
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def load_dotenv_file(file_path: str) -> bool:
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
                os.environ[key] = parse_env_value(raw_value)
                loaded = True
    except OSError:
        return False
    return loaded


def load_dotenv_from_cwd() -> list[str]:
    loaded_files: list[str] = []
    current = os.path.abspath(os.getcwd())
    while True:
        env_path = os.path.join(current, ".env")
        if os.path.isfile(env_path) and load_dotenv_file(env_path):
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
    object_key = f"video-remix/{ts}-{content_hash}{ext}"

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

    # Try PIL compression
    try:
        from PIL import Image
        import io
        img = Image.open(abs_path)
        # Resize if very large
        w, h = img.size
        while w * h > 4_000_000:  # ~4MP limit
            w, h = w * 3 // 4, h * 3 // 4
        if (w, h) != img.size:
            img = img.resize((w, h), Image.LANCZOS)
            log(f"resized to {w}x{h}")
        # Convert RGBA to RGB for JPEG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        # Try JPEG with decreasing quality
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
    # Try R2 upload first
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


def download_file(url: str, prompt: str, output_dir: str) -> str:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{safe_slug(prompt)}{guess_extension(url)}"
    local_path = os.path.join(output_dir, filename)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with (
            urllib.request.urlopen(req, timeout=300) as response,
            open(local_path, "wb") as f,
        ):
            f.write(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"failed to download video from {url}: HTTP {exc.code} {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"failed to download video from {url}: {exc.reason}"
        ) from exc
    return local_path


def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    body: dict | None = None,
    timeout: int = 60,
) -> dict:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
            detail = json.loads(error_body)
        except Exception:
            detail = error_body
        raise RuntimeError(
            f"HTTP {exc.code} {exc.reason} for {method} {url}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL error for {method} {url}: {exc.reason}") from exc


def normalise_status(raw: str, provider: str) -> str:
    mapping = STATUS_MAP_KIE if provider == "kie" else STATUS_MAP_GRSAI
    return mapping.get(raw.lower(), raw.lower())


def grsai_headers() -> dict[str, str]:
    headers = {"Authorization": f"Bearer {os.environ['GRSAI_API_KEY']}"}
    oss_id = os.environ.get("GRSAI_OSS_ID")
    if oss_id:
        headers["oss-id"] = oss_id
        headers["oss-path"] = os.environ.get("GRSAI_OSS_PATH", "aigc")
    return headers


def extract_job_id(data: dict) -> str:
    nested = data.get("data", {})
    if isinstance(nested, dict):
        for key in ("id", "jobId", "job_id", "taskId", "task_id"):
            value = nested.get(key)
            if value:
                return str(value)
    for key in ("id", "jobId", "taskId"):
        value = data.get(key)
        if value:
            return str(value)
    raise RuntimeError(f"cannot extract job id from response: {json.dumps(data)}")


def grsai_create_body(args: argparse.Namespace) -> tuple[str, dict]:
    if args.model == "sora-2":
        return (
            GRSAI_CREATE_ENDPOINTS[args.model],
            {
                "model": "sora-2",
                "prompt": args.prompt,
                "url": resolve_image_url(args.first_frame_url) if args.first_frame_url else (resolve_image_url(args.source_url) if args.source_url else None),
                "aspectRatio": args.aspect_ratio or "16:9",
                "duration": args.duration or 10,
                "webHook": "-1",
                "shutProgress": True,
            },
        )
    return (
        GRSAI_CREATE_ENDPOINTS[args.model],
        {
            "model": args.model,
            "prompt": args.prompt,
            "firstFrameUrl": resolve_image_url(args.first_frame_url) if args.first_frame_url else args.first_frame_url,
            "lastFrameUrl": resolve_image_url(args.last_frame_url) if args.last_frame_url else args.last_frame_url,
            "urls": resolve_image_urls(args.reference_images) or [],
            "aspectRatio": args.aspect_ratio or "16:9",
            "webHook": "-1",
            "shutProgress": False,
        },
    )


def grsai_create_job(args: argparse.Namespace) -> str:
    endpoint_path, body = grsai_create_body(args)
    url = f"https://api.grsai.com{endpoint_path}"
    log(f"grsai: creating job at {url} with model={args.model}")
    response = http_request(url, method="POST", headers=grsai_headers(), body=body)
    if response.get("code", 0) != 0:
        raise RuntimeError(f"grsai create-job error: {response.get('msg') or response}")
    job_id = extract_job_id(response)
    log(f"grsai: job created - id={job_id}")
    return job_id


def grsai_poll_result(job_id: str, timeout: int) -> str:
    url = "https://api.grsai.com/v1/draw/result"
    deadline = time.monotonic() + timeout
    while True:
        time.sleep(POLL_INTERVAL)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"grsai: job {job_id} timed out after {timeout}s")
        response = http_request(
            url, method="POST", headers=grsai_headers(), body={"id": job_id}
        )
        if response.get("code", 0) != 0:
            raise RuntimeError(f"grsai poll error: {response.get('msg') or response}")
        data = response.get("data", {})
        raw_status = data.get("status", "")
        status = normalise_status(raw_status, "grsai")
        log(f"grsai: job {job_id} status={status} (raw={raw_status})")
        if status == "failed":
            error = (
                data.get("failure_reason") or data.get("error") or "generation failed"
            )
            raise RuntimeError(f"grsai: job {job_id} failed - {error}")
        if status == "succeeded":
            if isinstance(data.get("url"), str) and data.get("url"):
                return data["url"]
            results = data.get("results")
            if isinstance(results, list) and results:
                result_url = results[0].get("url")
                if result_url:
                    return result_url
            raise RuntimeError(
                f"grsai: succeeded but no video url found in {json.dumps(data)}"
            )


def kie_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['KIE_API_KEY']}"}


def build_hailuo_payload(args: argparse.Namespace, model_id: str) -> dict:
    has_image = bool(args.first_frame_url)
    quality = "pro" if args.model == "hailuo-2.3" else "standard"
    full_model_id = (
        f"{model_id}-image-to-video-{quality}"
        if has_image
        else f"{model_id}-text-to-video-{quality}"
    )
    payload = {
        "model": full_model_id,
        "input": {
            "prompt": args.prompt,
            "duration": str(args.duration or 6),
            "resolution": (args.resolution or "1080p").upper(),
        },
    }
    if has_image:
        payload["input"]["image_url"] = resolve_image_url(args.first_frame_url)
    return payload


def build_kling_payload(args: argparse.Namespace, model_id: str) -> dict:
    has_image = bool(args.first_frame_url)
    full_model_id = (
        f"{model_id}/image-to-video" if has_image else f"{model_id}/text-to-video"
    )
    payload = {
        "model": full_model_id,
        "input": {
            "prompt": args.prompt,
            "sound": False,
            "duration": str(args.duration or 5),
        },
    }
    if has_image:
        payload["input"]["image_urls"] = [resolve_image_url(args.first_frame_url)]
    return payload


def build_grok_payload(args: argparse.Namespace, model_id: str) -> dict:
    has_image = bool(args.first_frame_url)
    full_model_id = (
        f"{model_id}/image-to-video" if has_image else f"{model_id}/text-to-video"
    )
    payload = {
        "model": full_model_id,
        "input": {
            "prompt": args.prompt,
            "mode": "normal",
        },
    }
    if has_image:
        payload["input"]["image_urls"] = [resolve_image_url(args.first_frame_url)]
    return payload


def build_seedance_payload(args: argparse.Namespace, model_id: str) -> dict:
    has_image = bool(args.first_frame_url)
    full_model_id = (
        f"{model_id}-image-to-video" if has_image else f"{model_id}-text-to-video"
    )
    payload = {
        "model": full_model_id,
        "input": {
            "prompt": args.prompt,
            "resolution": args.resolution or "1080p",
            "duration": str(args.duration or 5),
        },
    }
    if has_image:
        payload["input"]["image_url"] = resolve_image_url(args.first_frame_url)
    if args.model == "seedance-1.0-pro":
        payload["input"].update(
            {
                "camera_fixed": False,
                "seed": -1,
                "enable_safety_checker": False,
            }
        )
    return payload


def build_sora2_payload(args: argparse.Namespace) -> dict:
    image_urls: list[str] = []
    if args.first_frame_url:
        image_urls.append(resolve_image_url(args.first_frame_url))
    ratio = args.aspect_ratio or "16:9"
    payload = {
        "model": "sora-2-image-to-video" if image_urls else "sora-2-text-to-video",
        "input": {
            "prompt": args.prompt,
            "aspect_ratio": "portrait" if ratio == "9:16" else "landscape",
            "n_frames": "15" if (args.duration or 10) > 10 else "10",
            "remove_watermark": True,
        },
    }
    if image_urls:
        payload["input"]["image_urls"] = image_urls
    return payload


def build_veo_payload(args: argparse.Namespace, model_id: str) -> dict:
    payload = {
        "model": model_id,
        "prompt": args.prompt,
        "aspect_ratio": args.aspect_ratio or "16:9",
        "enableTranslation": True,
    }
    if args.reference_images:
        payload["generationType"] = "REFERENCE_2_VIDEO"
        payload["imageUrls"] = resolve_image_urls(args.reference_images)
    elif args.first_frame_url or args.last_frame_url:
        image_urls: list[str] = []
        if args.first_frame_url:
            image_urls.append(resolve_image_url(args.first_frame_url))
        if args.last_frame_url:
            image_urls.append(resolve_image_url(args.last_frame_url))
        payload["generationType"] = "FIRST_AND_LAST_FRAMES_2_VIDEO"
        payload["imageUrls"] = image_urls
    else:
        payload["generationType"] = "TEXT_2_VIDEO"
    return payload


def kie_create_job(args: argparse.Namespace) -> str:
    if args.model == "mock-video-lab":
        raise RuntimeError("mock provider is not implemented in this standalone script")
    model_id = KIE_MODEL_MAPPING[args.model]
    if args.model.startswith("veo3"):
        payload = build_veo_payload(args, model_id)
        url = "https://api.kie.ai/api/v1/veo/generate"
    elif args.model.startswith("hailuo"):
        payload = build_hailuo_payload(args, model_id)
        url = "https://api.kie.ai/api/v1/jobs/createTask"
    elif args.model.startswith("kling"):
        payload = build_kling_payload(args, model_id)
        url = "https://api.kie.ai/api/v1/jobs/createTask"
    elif args.model.startswith("grok"):
        payload = build_grok_payload(args, model_id)
        url = "https://api.kie.ai/api/v1/jobs/createTask"
    elif args.model.startswith("seedance"):
        payload = build_seedance_payload(args, model_id)
        url = "https://api.kie.ai/api/v1/jobs/createTask"
    elif args.model == "sora-2":
        payload = build_sora2_payload(args)
        url = "https://api.kie.ai/api/v1/jobs/createTask"
    else:
        raise RuntimeError(f"unsupported model for kie provider: {args.model}")
    log(f"kie: creating job at {url} with model={args.model}")
    response = http_request(url, method="POST", headers=kie_headers(), body=payload)
    if response.get("code") != 200:
        raise RuntimeError(f"kie create-job error: {response.get('msg') or response}")
    job_id = extract_job_id(response)
    log(f"kie: job created - taskId={job_id}")
    return job_id


def kie_poll_result(job_id: str, args: argparse.Namespace) -> str:
    deadline = time.monotonic() + args.timeout
    while True:
        time.sleep(POLL_INTERVAL)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"kie: job {job_id} timed out after {args.timeout}s")
        if args.model.startswith("veo3"):
            url = f"https://api.kie.ai/api/v1/veo/record-info?taskId={job_id}"
            response = http_request(url, method="GET", headers=kie_headers())
            if response.get("code") != 200:
                raise RuntimeError(
                    f"kie veo poll error: {response.get('msg') or response}"
                )
            data = response.get("data", {})
            success_flag = data.get("successFlag", 0)
            status = {0: "processing", 1: "succeeded", 2: "failed", 3: "failed"}.get(
                success_flag, "processing"
            )
            log(f"kie: veo job {job_id} status={status} (raw={success_flag})")
            if status == "failed":
                raise RuntimeError(
                    f"kie: job {job_id} failed - {data.get('errorMessage') or data}"
                )
            if status == "succeeded":
                result_urls = data.get("response", {}).get("resultUrls", [])
                if result_urls:
                    return result_urls[0]
                raise RuntimeError(
                    f"kie: veo succeeded but no resultUrls in {json.dumps(data)}"
                )
        else:
            url = f"https://api.kie.ai/api/v1/jobs/recordInfo?taskId={job_id}"
            response = http_request(url, method="GET", headers=kie_headers())
            if response.get("code") != 200:
                raise RuntimeError(f"kie poll error: {response.get('msg') or response}")
            data = response.get("data", {})
            raw_status = data.get("state", "")
            status = normalise_status(raw_status, "kie")
            log(f"kie: job {job_id} status={status} (raw={raw_status})")
            if status == "failed":
                raise RuntimeError(
                    f"kie: job {job_id} failed - {data.get('failMsg') or data}"
                )
            if status == "succeeded":
                result_json = data.get("resultJson")
                if not result_json:
                    raise RuntimeError(
                        f"kie: succeeded but no resultJson in {json.dumps(data)}"
                    )
                try:
                    result_obj = json.loads(result_json)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"kie: failed to parse resultJson: {exc}"
                    ) from exc
                result_urls = result_obj.get("resultUrls", [])
                if result_urls:
                    return result_urls[0]
                raise RuntimeError(f"kie: no resultUrls in resultJson: {result_json}")


def mock_generate(args: argparse.Namespace) -> str:
    if os.environ.get("MOCK_VIDEO_PROVIDER_ENABLED") != "true":
        raise RuntimeError("mock provider requires MOCK_VIDEO_PROVIDER_ENABLED=true")
    url = os.environ.get("MOCK_VIDEO_PROVIDER_SUCCESS_URL")
    if not url:
        raise RuntimeError("mock provider requires MOCK_VIDEO_PROVIDER_SUCCESS_URL")
    log(f"mock: returning success url for model={args.model}")
    return url


def generate(args: argparse.Namespace) -> tuple[str, str]:
    if args.provider == "grsai":
        job_id = grsai_create_job(args)
        return job_id, grsai_poll_result(job_id, args.timeout)
    if args.provider == "kie":
        job_id = kie_create_job(args)
        return job_id, kie_poll_result(job_id, args)
    return "mock-job", mock_generate(args)


def validate(args: argparse.Namespace) -> str | None:
    if args.provider == "grsai" and not os.environ.get("GRSAI_API_KEY"):
        return "GRSAI_API_KEY environment variable is required for the grsai provider"
    if args.provider == "kie" and not os.environ.get("KIE_API_KEY"):
        return "KIE_API_KEY environment variable is required for the kie provider"
    if (
        args.provider == "mock"
        and os.environ.get("MOCK_VIDEO_PROVIDER_ENABLED") != "true"
    ):
        return "MOCK_VIDEO_PROVIDER_ENABLED=true is required for the mock provider"
    supported_models = PROVIDER_MODELS.get(args.provider, set())
    if args.model not in supported_models:
        return f"provider {args.provider} does not support model {args.model}"
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate videos via grsai, kie, or mock providers."
    )
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default="veo3.1-fast")
    parser.add_argument("--provider", default="grsai", choices=["grsai", "kie", "mock"])
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--duration", type=int, default=None)
    parser.add_argument("--resolution", default=None)
    parser.add_argument("--source-url", default=None)
    parser.add_argument("--first-frame-url", default=None)
    parser.add_argument("--last-frame-url", default=None)
    parser.add_argument("--reference-images", nargs="*", default=None)
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--timeout", type=int, default=600)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    loaded_env_files = load_dotenv_from_cwd()
    for env_path in loaded_env_files:
        log(f"loaded env - {env_path}")
    error = validate(args)
    if error:
        json_output({"status": "config_error", "message": error})
        sys.exit(2)
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    try:
        job_id, url = generate(args)
        saved_path = download_file(url, args.prompt, output_dir)
        log(f"saved locally - {saved_path}")
        json_output(
            {
                "status": "success",
                "job_id": job_id,
                "urls": [url],
                "saved_paths": [saved_path],
                "output_dir": output_dir,
                "model": args.model,
                "provider": args.provider,
            }
        )
        sys.exit(0)
    except Exception as exc:
        log(f"error: {exc}")
        json_output(
            {
                "status": "error",
                "message": str(exc),
                "model": args.model,
                "provider": args.provider,
            }
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
