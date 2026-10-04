"""Optional authenticated ComfyUI bridge client and validated web image encoding."""

import io
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps


class ComfyUnavailable(RuntimeError):
    pass


@dataclass
class ImageAsset:
    content: bytes
    width: int
    height: int
    provider: str


def encode_image(content: bytes, image_format: str, quality: int, provider: str) -> ImageAsset:
    """Decode before accepting a provider result; preserve actual image dimensions."""
    if not content or len(content) > 25 * 1024 * 1024:
        raise ValueError("Image response was empty or too large")
    with Image.open(io.BytesIO(content)) as original:
        if original.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("Unsupported generated image format")
        width, height = original.size
        if min(width, height) < 400 or width * height > 24_000_000:
            raise ValueError("Generated image dimensions were outside the allowed range")
        original.load()
        if original.format == image_format.upper() and original.getexif().get(274, 1) == 1:
            # OpenAI already compresses its output. Avoid a second lossy encoding.
            return ImageAsset(content, width, height, provider)
        picture = ImageOps.exif_transpose(original).convert("RGB")
        result = io.BytesIO()
        options = {"quality": quality} if image_format in {"webp", "jpeg"} else {}
        picture.save(result, format=image_format.upper(), **options)
        return ImageAsset(result.getvalue(), *picture.size, provider)


class ComfyImageClient:
    """Try ComfyUI first, disabling it for the rest of this post after any failure."""

    def __init__(self, url=None, token=None, generation_timeout=None):
        self.url = (url if url is not None else os.environ.get("COMFYUI_BRIDGE_URL", "")).rstrip("/")
        self.token = token if token is not None else os.environ.get("COMFYUI_BRIDGE_TOKEN", "")
        try:
            timeout = float(generation_timeout if generation_timeout is not None
                            else os.environ.get("COMFYUI_GENERATION_TIMEOUT", "300"))
        except (ValueError, TypeError):
            timeout = 300
        self.generation_timeout = max(1, min(900, timeout))
        self.disabled_reason = ""
        self.session = requests.Session()

    @property
    def configured(self):
        return bool(self.url and self.token)

    def _request(self, method, path, deadline, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ComfyUnavailable("ComfyUI generation timed out")
        try:
            response = self.session.request(
                method, self.url + path,
                headers={"Authorization": "Bearer " + self.token},
                timeout=(min(5, remaining), min(10, remaining)),
                allow_redirects=False, **kwargs,
            )
        except requests.RequestException as exc:
            # Requests errors can include private URLs. Keep logs and Telegram secret-free.
            raise ComfyUnavailable("ComfyUI bridge could not be reached") from exc
        if response.status_code not in {200, 201, 202}:
            raise ComfyUnavailable(f"ComfyUI bridge returned HTTP {response.status_code}")
        return response

    def generate(self, prompt):
        if not self.configured:
            raise ComfyUnavailable("ComfyUI bridge is not configured")
        if self.disabled_reason:
            raise ComfyUnavailable(self.disabled_reason)
        parsed = urlparse(self.url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise ComfyUnavailable("ComfyUI bridge requires HTTPS or a loopback address")
        deadline = time.monotonic() + self.generation_timeout
        job_id = None
        try:
            health = self._request("GET", "/health", deadline).json()
            if health.get("ready") is not True:
                raise ComfyUnavailable("ComfyUI is offline or busy")
            submitted = self._request("POST", "/jobs", deadline, json={"prompt": prompt}).json()
            job_id = submitted["job_id"]
            if not isinstance(job_id, str) or not job_id.isalnum():
                raise ComfyUnavailable("ComfyUI bridge returned an invalid job ID")
            while True:
                status = self._request("GET", f"/jobs/{job_id}", deadline).json()
                if status.get("status") == "completed":
                    return self._request("GET", f"/jobs/{job_id}/image", deadline).content
                if status.get("status") not in {"queued", "running"}:
                    raise ComfyUnavailable("ComfyUI image generation failed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ComfyUnavailable("ComfyUI generation timed out")
                time.sleep(min(2, remaining))
        except (KeyError, TypeError, ValueError) as exc:
            raise ComfyUnavailable("ComfyUI bridge returned an invalid response") from exc
        finally:
            if job_id:
                # Delete only our queued job; never interrupt another user's GPU job.
                try:
                    self._request("POST", f"/jobs/{job_id}/cancel", time.monotonic() + 3)
                except ComfyUnavailable:
                    pass

    def disable(self, reason):
        self.disabled_reason = reason


def generate_with_fallback(scene, style_template, openai_generate, image_format,
                           quality, client, log=print):
    """Validate ComfyUI output before committing to it; OpenAI remains the fallback."""
    note = ""
    if client.configured and not client.disabled_reason:
        try:
            raw = client.generate(style_template.format(scene=scene))
            return encode_image(raw, image_format, quality, "ComfyUI"), note
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ComfyUnavailable) else "ComfyUI returned an unusable image"
            client.disable(reason)
            note = f"ComfyUI unavailable: {reason}; using OpenAI for the rest of this post."
            log(note)
    return encode_image(openai_generate(scene), image_format, quality, "OpenAI"), note
