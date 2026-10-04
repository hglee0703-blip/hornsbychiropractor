"""Exercise ComfyUI success and failure through real loopback HTTP, without paid APIs."""

import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blog_images import ComfyImageClient, ComfyUnavailable, generate_with_fallback
from comfy_bridge import ComfyBridge, make_server


def png_bytes(width=640, height=480):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#e0d3b4").save(buffer, format="PNG")
    return buffer.getvalue()


class ImageFailoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = "test-token-" + "a" * 48
        cls.state = {}

        class BackendHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, payload):
                content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def do_GET(self):
                if cls.state.get("offline"):
                    return self.reply(503, {})
                if self.path == "/queue":
                    running = [[0, "somebody-elses-job"]] if cls.state.get("busy") else []
                    pending = [[0, "backend-prompt-id"]] if cls.state.get("pending") and cls.state.get("posts") else []
                    return self.reply(200, {"queue_running": running, "queue_pending": pending})
                if self.path.startswith("/history/"):
                    if cls.state.get("pending"):
                        return self.reply(200, {})
                    status = "error" if cls.state.get("failed") else "success"
                    outputs = {} if status == "error" or cls.state.get("no_image") else {
                        "9": {"images": [{"filename": "blog.png", "subfolder": "hornsby-blog", "type": "output"}]}
                    }
                    return self.reply(200, {"backend-prompt-id": {
                        "status": {"status_str": status, "completed": True}, "outputs": outputs,
                    }})
                if self.path.startswith("/view?"):
                    return self.reply(200, cls.state.get("image", png_bytes()))
                self.reply(404, {})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                cls.state.setdefault("posts", []).append((self.path, payload))
                if self.path == "/prompt":
                    return self.reply(200, {"prompt_id": "backend-prompt-id", "node_errors": {}})
                self.reply(200, {})

        cls.backend = ThreadingHTTPServer(("127.0.0.1", 0), BackendHandler)
        cls.backend_thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.backend_thread.start()
        cls.bridge = ComfyBridge({
            "token": cls.token,
            "comfy_url": f"http://127.0.0.1:{cls.backend.server_port}",
            "workflow": str(Path(__file__).with_name("comfy_workflow.json")),
        })
        cls.server = make_server(cls.bridge, 0)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        for server in (cls.server, cls.backend):
            server.shutdown()
            server.server_close()

    def setUp(self):
        self.state.clear()
        self.bridge.jobs.clear()
        self.client = ComfyImageClient(self.url, self.token, 5)
        self.openai = Mock(return_value=png_bytes(800, 600))

    def generate(self):
        return generate_with_fallback("a person at a desk", "Illustrate {scene}",
                                      self.openai, "webp", 82, self.client, lambda _: None)

    def test_success_uses_comfy_and_actual_image_dimensions(self):
        asset, note = self.generate()
        self.assertEqual((asset.provider, asset.width, asset.height), ("ComfyUI", 640, 480))
        self.assertEqual(note, "")
        self.openai.assert_not_called()
        with Image.open(io.BytesIO(asset.content)) as picture:
            self.assertEqual(picture.format, "WEBP")
        workflow = self.state["posts"][0][1]["prompt"]
        self.assertEqual(workflow["27"]["inputs"]["text"], "Illustrate a person at a desk")
        self.assertGreater(workflow["3"]["inputs"]["seed"], 0)
        self.assertTrue(workflow["9"]["inputs"]["filename_prefix"].startswith("hornsby-blog/"))
        # Cancellation may delete only this job; no global interrupt is issued.
        self.assertEqual(self.state["posts"][-1], ("/queue", {"delete": ["backend-prompt-id"]}))

    def assert_fallback(self):
        asset, note = self.generate()
        self.assertEqual((asset.provider, asset.width, asset.height), ("OpenAI", 800, 600))
        self.assertIn("using OpenAI", note)
        self.openai.assert_called_once()
        posts = len(self.state.get("posts", []))
        # The second illustration bypasses the failed provider immediately.
        asset, note = self.generate()
        self.assertEqual(asset.provider, "OpenAI")
        self.assertEqual(note, "")
        self.assertEqual(len(self.state.get("posts", [])), posts)

    def test_offline_falls_back_and_bypasses_comfy_for_second_image(self):
        self.state["offline"] = True
        self.assert_fallback()

    def test_busy_gpu_falls_back_without_submitting_or_interrupting(self):
        self.state["busy"] = True
        self.assert_fallback()
        self.assertNotIn("posts", self.state)

    def test_execution_error_falls_back(self):
        self.state["failed"] = True
        self.assert_fallback()

    def test_completed_without_image_falls_back(self):
        self.state["no_image"] = True
        self.assert_fallback()

    def test_invalid_image_falls_back(self):
        self.state["image"] = b"not an image"
        self.assert_fallback()

    def test_tiny_image_falls_back(self):
        self.state["image"] = png_bytes(32, 32)
        self.assert_fallback()

    def test_timeout_falls_back_and_deletes_only_its_pending_job(self):
        self.state["pending"] = True
        self.client = ComfyImageClient(self.url, self.token, 1)
        self.assert_fallback()
        self.assertEqual(self.state["posts"][-1], ("/queue", {"delete": ["backend-prompt-id"]}))

    def test_bad_auth_falls_back(self):
        self.client = ComfyImageClient(self.url, "wrong-token", 5)
        self.assert_fallback()

    def test_unconfigured_uses_openai(self):
        self.client = ComfyImageClient("", "", 5)
        asset, note = self.generate()
        self.assertEqual(asset.provider, "OpenAI")
        self.assertEqual(note, "")
        self.assertNotIn("posts", self.state)

    def test_unreachable_bridge_falls_back(self):
        # Bound but non-listening socket gives a deterministic connection refusal.
        import socket
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            self.client = ComfyImageClient(f"http://127.0.0.1:{closed.getsockname()[1]}", self.token, 1)
            self.assert_fallback()

    def test_public_http_rejected_before_token_is_sent(self):
        self.client = ComfyImageClient("http://example.com", self.token, 5)
        self.assert_fallback()

    def test_arbitrary_routes_files_jobs_and_workflows_are_inaccessible(self):
        headers = {"Authorization": "Bearer " + self.token}
        for path in ("/", "/view?filename=config.json", "/object_info", "/history", "/jobs/" + "f" * 32):
            response = requests.get(self.url + path, headers=headers, timeout=2)
            self.assertEqual(response.status_code, 404)
        response = requests.post(self.url + "/jobs", headers=headers,
                                 json={"prompt": "valid", "workflow": {"evil": {}}}, timeout=2)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("posts", self.state)
        response = requests.get(self.url + "/health", timeout=2)
        self.assertEqual(response.status_code, 401)

    def test_openai_failure_propagates_after_comfy_fallback(self):
        self.state["offline"] = True
        self.openai.side_effect = RuntimeError("OpenAI failed")
        with self.assertRaisesRegex(RuntimeError, "OpenAI failed"):
            self.generate()

    def test_failed_provider_is_retried_on_next_blog_post(self):
        self.state["offline"] = True
        self.generate()
        self.state["offline"] = False
        self.client = ComfyImageClient(self.url, self.token, 5)
        self.openai.reset_mock()
        asset, _ = self.generate()
        self.assertEqual(asset.provider, "ComfyUI")
        self.openai.assert_not_called()

    def test_blog_builder_records_fallback_and_correct_metadata_for_both_images(self):
        import generate_blog
        self.state["offline"] = True
        article = {"slug": "test-only", "title": "Desk posture", "image_prompts": [
            {"scene": "desk", "alt": "A person at a desk"},
            {"scene": "standing", "alt": "A person standing"},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(generate_blog, "ASSETS_IMG_DIR", Path(directory)), \
                    patch.object(generate_blog, "ComfyImageClient", return_value=self.client), \
                    patch.object(generate_blog, "_generate_image_bytes", self.openai):
                images, notes = generate_blog.build_generated_images(article)
            self.assertEqual(len(images), 2)
            self.assertEqual(len(notes), 1)
            for item in images:
                self.assertEqual((item["provider"], item["width"], item["height"]), ("OpenAI", 800, 600))
                self.assertTrue((Path(directory) / Path(item["public_path"]).name).is_file())
            self.assertEqual(self.openai.call_count, 2)


if __name__ == "__main__":
    unittest.main()
