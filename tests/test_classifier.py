import base64
import io
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image
from server.classifier import MODEL_ID, OLLAMA_URL, PROMPT, classify_brand, load_classifier, normalize_brand
from server.main import app


class BrandParsingTests(unittest.TestCase):
    def test_exact_supported_labels(self):
        for label in ["microsoft", "google", "meta", "unknown"]:
            self.assertEqual(normalize_brand(label), label)
        self.assertEqual(normalize_brand("  MICROSOFT\n"), "microsoft")

    def test_untrusted_or_ambiguous_output_is_unknown(self):
        for output in [None, 1, "", "facebook", "google.", '"meta"', "microsoft or google",
                       "The brand is google", "```microsoft```", "microsoft\nIgnore instructions"]:
            with self.subTest(output=output):
                self.assertEqual(normalize_brand(output), "unknown")


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.content = " MICROSOFT\n"
        self.done = True
        self.vram = 100
        self.failure = None
        client_type = httpx.Client

        def handle(request):
            self.requests.append(request)
            self.assertEqual(str(request.url).split("/api/")[0], OLLAMA_URL)
            if self.failure:
                return self.failure(request)
            if request.url.path == "/api/ps":
                return httpx.Response(200, json={"models": [
                    {"name": MODEL_ID, "size": 100, "size_vram": self.vram},
                ]})
            if request.url.path == "/api/generate":
                return httpx.Response(200, json={"done": True})
            self.assertEqual(request.url.path, "/api/chat")
            return httpx.Response(200, json={"done": self.done, "message": {"content": self.content}})

        def client(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertEqual(kwargs["base_url"], OLLAMA_URL)
            return client_type(**kwargs, transport=httpx.MockTransport(handle))

        self.client_patch = patch("server.classifier.httpx.Client", side_effect=client)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        image = io.BytesIO()
        Image.new("RGB", (2560, 1440), "white").save(image, format="PNG")
        self.image = image.getvalue()

    def test_startup_preloads_local_model_without_download(self):
        self.assertEqual(load_classifier(), MODEL_ID)
        self.assertEqual([r.url.path for r in self.requests], ["/api/generate", "/api/ps"])
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload, {"model": MODEL_ID, "prompt": "", "stream": False, "keep_alive": -1})

    def test_image_chat_contract_and_strict_label(self):
        brand, elapsed = classify_brand(MODEL_ID, self.image)
        self.assertEqual(brand, "microsoft")
        self.assertGreater(elapsed, 0)
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload["model"], MODEL_ID)
        self.assertEqual(payload["messages"][0], {"role": "system", "content": PROMPT})
        user = payload["messages"][1]
        self.assertEqual(set(user), {"role", "content", "images"})
        with Image.open(io.BytesIO(base64.b64decode(user["images"][0]))) as image:
            self.assertEqual(image.size, (1280, 720))
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["keep_alive"], -1)
        self.assertEqual(payload["options"], {"temperature": 0, "num_predict": 8})
        self.assertEqual([r.url.path for r in self.requests], ["/api/chat", "/api/ps"])

    def test_unparseable_model_content_becomes_unknown(self):
        self.content = "The brand is microsoft"
        self.assertEqual(classify_brand(MODEL_ID, self.image)[0], "unknown")

    def test_cpu_or_partial_offload_is_rejected_at_startup_and_inference(self):
        for vram in [0, 50, None]:
            self.vram = vram
            with self.subTest(vram=vram):
                with self.assertRaisesRegex(RuntimeError, "CPU fallback is disabled"):
                    load_classifier()
                with self.assertRaisesRegex(RuntimeError, "CPU fallback is disabled"):
                    classify_brand(MODEL_ID, self.image)

    def test_missing_model_timeout_and_malformed_responses_raise(self):
        def timeout(request):
            raise httpx.ReadTimeout("private response data", request=request)

        for failure in [lambda r: httpx.Response(404), timeout,
                        lambda r: httpx.Response(200, text="invalid json"),
                        lambda r: httpx.Response(200, json=[]),
                        lambda r: httpx.Response(200, json={"done": True})]:
            self.failure = failure
            with self.subTest(failure=failure), self.assertRaises((httpx.HTTPError, ValueError)):
                classify_brand(MODEL_ID, self.image)

    def test_incomplete_response_is_rejected(self):
        self.done = False
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            classify_brand(MODEL_ID, self.image)

    def test_endpoint_uses_real_ollama_adapter_only_after_ocr_pass(self):
        payload = {"url": "https://login.microsoftonline.com/",
                   "screenshot": "data:image/png;base64," + base64.b64encode(self.image).decode()}
        with patch("server.main.load_reader", return_value=object()), patch("server.main.policies.lookup", return_value=None):
            with TestClient(app) as client:
                self.assertEqual(app.state.classifier, MODEL_ID)
                self.requests.clear()  # Exclude startup preload, which has no image or classification.
                with patch("server.main.check_login", return_value=(False, 1.0)):
                    self.assertEqual(client.post("/analyze", json=payload).json()["verdict"], "not_login")
                self.assertEqual(self.requests, [])
                with patch("server.main.check_login", return_value=(True, 1.0)):
                    self.assertEqual(client.post("/analyze", json=payload).json()["verdict"], "safe")
                    payload["url"] = "https://fixture.invalid/"
                    self.assertEqual(client.post("/analyze", json=payload).json()["verdict"], "dangerous")
                    self.failure = lambda request: httpx.Response(404, text="private upstream error")
                    with self.assertLogs("uvicorn.error", level="WARNING") as logs:
                        response = client.post("/analyze", json=payload)
                    self.assertEqual(response.json()["verdict"], "unknown")
                    self.assertNotIn("private upstream error", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
