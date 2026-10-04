"""Token-protected, fixed-workflow ComfyUI bridge for GitHub blog automation.

Only blog job submission/status/images are exposed. The ComfyUI interface,
arbitrary workflows, local files and other users' jobs are never proxied.
"""

import argparse
import copy
import hmac
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import requests

LOGGER = logging.getLogger("comfy-bridge")
MAX_IMAGE_BYTES = 25 * 1024 * 1024


class BridgeError(RuntimeError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class ComfyBridge:
    def __init__(self, config):
        self.token = config["token"]
        if len(self.token) < 32:
            raise ValueError("Bridge token must contain at least 32 characters")
        self.url = config.get("comfy_url", "http://127.0.0.1:8188").rstrip("/")
        if not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):\d+", self.url):
            raise ValueError("ComfyUI backend must be a local loopback address")
        self.workflow = json.loads(Path(config["workflow"]).read_text(encoding="utf-8"))
        self.prompt_node = str(config.get("prompt_node", "27"))
        self.seed_node = str(config.get("seed_node", "3"))
        self.output_node = str(config.get("output_node", "9"))
        if (self.workflow[self.prompt_node]["class_type"] != "CLIPTextEncode"
                or self.workflow[self.output_node]["class_type"] != "SaveImage"):
            raise ValueError("Configure the positive CLIPTextEncode and SaveImage node IDs")
        self.workflow[self.seed_node]["inputs"]["seed"]
        self.jobs = {}
        self.lock = threading.RLock()

    def _request(self, method, path, **kwargs):
        try:
            result = requests.request(method, self.url + path, timeout=(2, 5), **kwargs)
        except requests.RequestException as exc:
            raise BridgeError("ComfyUI is offline") from exc
        if result.status_code != 200:
            raise BridgeError("ComfyUI rejected the request")
        return result

    def ready(self):
        queue = self._request("GET", "/queue").json()
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise BridgeError("ComfyUI is busy", 409)
        return {"ready": True}

    def submit(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16_000:
            raise BridgeError("A prompt of 1-16000 characters is required", 400)
        with self.lock:
            self.ready()
            now = time.monotonic()
            self.jobs = {key: value for key, value in self.jobs.items() if now - value["created"] < 900}
            if len(self.jobs) >= 16:
                raise BridgeError("Too many recent blog jobs", 429)
            job_id = secrets.token_hex(16)
            workflow = copy.deepcopy(self.workflow)
            workflow[self.prompt_node]["inputs"]["text"] = prompt
            workflow[self.seed_node]["inputs"]["seed"] = secrets.randbits(53)
            workflow[self.output_node]["inputs"]["filename_prefix"] = f"hornsby-blog/{job_id}"
            result = self._request("POST", "/prompt", json={"prompt": workflow, "client_id": "hornsby-blog"}).json()
            prompt_id = result.get("prompt_id")
            if not isinstance(prompt_id, str) or not prompt_id or result.get("node_errors"):
                raise BridgeError("ComfyUI workflow validation failed")
            self.jobs[job_id] = {"prompt_id": prompt_id, "created": now}
            LOGGER.info("Submitted blog illustration %s", job_id)
            return {"job_id": job_id}

    def _job(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            raise BridgeError("Unknown blog job", 404)
        return job

    def status(self, job_id):
        job = self._job(job_id)
        prompt_id = job["prompt_id"]
        history = self._request("GET", "/history/" + quote(prompt_id, safe="")).json()
        record = history.get(prompt_id)
        if record:
            state = record.get("status", {})
            if (state.get("status_str") == "error" or any(
                    message[0] in {"execution_error", "execution_interrupted"}
                    for message in state.get("messages", []) if message)):
                return {"status": "failed"}
            images = record.get("outputs", {}).get(self.output_node, {}).get("images", [])
            if images:
                job["image"] = images[0]
                return {"status": "completed"}
            if state.get("completed"):
                return {"status": "failed"}
        queue = self._request("GET", "/queue").json()
        if any(item[1] == prompt_id for item in queue.get("queue_running", [])):
            return {"status": "running"}
        if any(item[1] == prompt_id for item in queue.get("queue_pending", [])):
            return {"status": "queued"}
        return {"status": "failed"}

    def image(self, job_id):
        job = self._job(job_id)
        if self.status(job_id)["status"] != "completed":
            raise BridgeError("Blog image is not ready", 409)
        image = job["image"]
        # Only filenames returned by this job's fixed SaveImage node are accepted.
        response = self._request("GET", "/view", params={
            "filename": image["filename"], "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        }, stream=True)
        with response:
            chunks = []
            size = 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise BridgeError("Generated image is too large")
                chunks.append(chunk)
        return b"".join(chunks)

    def cancel(self, job_id):
        job = self._job(job_id)
        # /interrupt stops global execution, so never use it. Only delete our pending job.
        self._request("POST", "/queue", json={"delete": [job["prompt_id"]]})
        return {"ok": True}


def make_server(bridge, port):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_args):
            pass

        def send_json(self, status, payload):
            content = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def dispatch(self):
            expected = "Bearer " + bridge.token
            if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected.encode()):
                self.send_json(401, {"error": "Unauthorized"})
                return
            try:
                if self.command == "GET" and self.path == "/health":
                    self.send_json(200, bridge.ready())
                elif self.command == "POST" and self.path == "/jobs":
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 100_000:
                        raise BridgeError("Invalid request size", 400)
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict) or set(payload) != {"prompt"}:
                        raise BridgeError("Only a text prompt is accepted", 400)
                    self.send_json(201, bridge.submit(payload["prompt"]))
                else:
                    match = re.fullmatch(r"/jobs/([a-f0-9]{32})(/image|/cancel)?", self.path)
                    if not match:
                        raise BridgeError("Not found", 404)
                    job_id, suffix = match.groups()
                    if self.command == "GET" and suffix is None:
                        self.send_json(200, bridge.status(job_id))
                    elif self.command == "GET" and suffix == "/image":
                        content = bridge.image(job_id)
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Length", str(len(content)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(content)
                    elif self.command == "POST" and suffix == "/cancel":
                        self.send_json(200, bridge.cancel(job_id))
                    else:
                        raise BridgeError("Method not allowed", 405)
            except BridgeError as exc:
                self.send_json(exc.status, {"error": str(exc)})
            except (ValueError, KeyError, TypeError, IndexError):
                self.send_json(503, {"error": "Invalid ComfyUI response or request"})
            except (OSError, requests.RequestException):
                LOGGER.warning("Request failed while serving a blog image")

        do_GET = dispatch
        do_POST = dispatch

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def set_github_secret(gh, repo, name, value):
    result = subprocess.run([gh, "secret", "set", name, "--repo", repo],
                            input=value, text=True, capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("Could not update GitHub bridge secret")


def run_tunnel(config, stop):
    """Renew a temporary HTTPS tunnel and publish its address without exposing tokens."""
    gh, repo = config["gh"], config["repo"]
    while not stop.is_set():
        process = None
        try:
            set_github_secret(gh, repo, "COMFYUI_BRIDGE_TOKEN", config["token"])
            process = subprocess.Popen([
                config["cloudflared"], "--no-autoupdate", "tunnel", "--url",
                f"http://127.0.0.1:{config.get('port', 8796)}", "--protocol", "http2",
            ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            address = []
            announced = threading.Event()

            def read_output(output, addresses, event):
                for line in output:
                    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                    if match and not addresses:
                        addresses.append(match.group())
                        event.set()

            threading.Thread(target=read_output, args=(process.stdout, address, announced), daemon=True).start()
            deadline = time.monotonic() + 60
            while not announced.wait(1):
                if stop.is_set() or process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Tunnel did not start")
            set_github_secret(gh, repo, "COMFYUI_BRIDGE_URL", address[0])
            Path(config["state_file"]).write_text(json.dumps({"url": address[0], "pid": process.pid}), encoding="utf-8")
            LOGGER.info("HTTPS bridge connected; GitHub image fallback is enabled")
            while not stop.wait(5) and process.poll() is None:
                pass
        except Exception as exc:
            LOGGER.warning("Tunnel connection needs retry (%s)", type(exc).__name__)
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        stop.wait(10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--tunnel", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    handler = RotatingFileHandler(args.config.parent / "bridge.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    server = make_server(ComfyBridge(config), config.get("port", 8796))
    stop = threading.Event()
    tunnel = None
    if args.tunnel:
        tunnel = threading.Thread(target=run_tunnel, args=(config, stop), daemon=True)
        tunnel.start()
    LOGGER.info("Blog bridge listening on loopback")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if tunnel:
            tunnel.join(timeout=10)


if __name__ == "__main__":
    main()
