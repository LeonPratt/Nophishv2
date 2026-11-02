import base64
import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from server.main import MAX_REQUEST_BYTES, app


def screenshot():
    output = io.BytesIO()
    Image.new("RGB", (100, 60), "white").save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.policy_lookup = patch("server.main.policies.lookup", return_value=None)
        self.policy_lookup.start()
        self.addCleanup(self.policy_lookup.stop)
        self.loader = patch("server.main.load_reader", return_value=object())
        self.reader_factory = self.loader.start()
        self.addCleanup(self.loader.stop)
        self.model_loader = patch("server.main.load_classifier", return_value=object())
        self.model_factory = self.model_loader.start()
        self.addCleanup(self.model_loader.stop)
        self.classify_patch = patch("server.main.classify_brand", return_value=("microsoft", 20.0))
        self.classify = self.classify_patch.start()
        self.addCleanup(self.classify_patch.stop)
        self.client = TestClient(app).__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.payload = {"url": "http://127.0.0.1:8001/login.html", "screenshot": screenshot()}

    def test_login_classifies_same_screenshot_after_ocr(self):
        stages = []

        def ocr_result(*args):
            stages.append("ocr")
            return True, 12.0

        def brand_result(*args):
            stages.append("vlm")
            return "microsoft", 20.0

        self.classify.side_effect = brand_result
        with patch("server.main.check_login", side_effect=ocr_result) as ocr:
            response = self.client.post("/analyze", json=self.payload)
        self.assertEqual(stages, ["ocr", "vlm"])
        ocr.assert_called_once()
        self.assertIsInstance(ocr.call_args.args[1], bytes)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "dangerous")
        self.assertEqual(response.json()["brand"], "microsoft")
        self.classify.assert_called_once()
        self.assertEqual(self.classify.call_args.args[1], ocr.call_args.args[1])

    def test_same_screenshot_approved_and_unapproved_hosts(self):
        with patch("server.main.check_login", return_value=(True, 1.0)):
            for url, verdict in [("https://login.microsoftonline.com/", "safe"),
                                 ("https://login.microsoftonline.com.attacker.example/", "dangerous")]:
                response = self.client.post("/analyze", json={**self.payload, "url": url})
                self.assertEqual(response.json()["verdict"], verdict)
                self.assertEqual(response.json()["brand"], "microsoft")

    def test_non_login_exits_after_ocr(self):
        with patch("server.main.check_login", return_value=(False, 12.0)) as ocr:
            response = self.client.post("/analyze", json=self.payload)
        ocr.assert_called_once()
        self.assertEqual(response.json(), {
            "verdict": "not_login", "brand": None, "reason": "No login page detected"})
        self.classify.assert_not_called()

    def test_ocr_error_is_unknown(self):
        with patch("server.main.check_login", side_effect=RuntimeError("private OCR text")):
            with self.assertLogs("uvicorn.error", level="WARNING") as logs:
                response = self.client.post("/analyze", json=self.payload)
        self.assertEqual(response.json()["verdict"], "unknown")
        self.assertNotIn("private OCR text", " ".join(logs.output))
        self.classify.assert_not_called()

    def test_unavailable_reader_does_not_run_ocr(self):
        app.state.ocr_reader = None
        with patch("server.main.check_login") as ocr:
            response = self.client.post("/analyze", json=self.payload)
        ocr.assert_not_called()
        self.classify.assert_not_called()
        self.assertEqual(response.json()["verdict"], "unknown")

    def test_reader_loaded_once_for_multiple_requests(self):
        with patch("server.main.check_login", return_value=(False, 1.0)):
            for _ in range(2):
                self.client.post("/analyze", json=self.payload)
        self.reader_factory.assert_called_once()
        self.model_factory.assert_called_once()
        self.classify.assert_not_called()

    def test_failed_or_invalid_vlm_is_unknown(self):
        with patch("server.main.check_login", return_value=(True, 1.0)):
            self.classify.return_value = ("microsoft extra untrusted text", 1.0)
            response = self.client.post("/analyze", json=self.payload)
            self.assertEqual(response.json()["brand"], "unknown")
            self.classify.side_effect = RuntimeError("private model output")
            with self.assertLogs("uvicorn.error", level="WARNING") as logs:
                response = self.client.post("/analyze", json=self.payload)
            self.assertNotIn("private model output", " ".join(logs.output))
            self.assertEqual(response.json()["verdict"], "unknown")
            self.assertEqual(response.json()["brand"], "unknown")

    def test_unavailable_vlm_still_allows_non_login_result(self):
        app.state.classifier = None
        with patch("server.main.check_login", return_value=(False, 1.0)):
            self.assertEqual(self.client.post("/analyze", json=self.payload).json()["verdict"], "not_login")
        with patch("server.main.check_login", return_value=(True, 1.0)):
            self.assertEqual(self.client.post("/analyze", json=self.payload).json()["brand"], "unknown")
        self.classify.assert_not_called()

    def test_invalid_urls(self):
        for url in [None, "file:///test", "https://", "http://host:bad", "not a URL"]:
            with self.subTest(url=url):
                response = self.client.post("/analyze", json={**self.payload, "url": url})
                self.assertEqual(response.status_code, 400)

    def test_bad_screenshots(self):
        corrupt_png = bytearray(base64.b64decode(self.payload["screenshot"].split(",")[1]))
        corrupt_png[-5] ^= 1  # Break the final chunk checksum.
        for value in [None, "data:image/png;base64,???", "data:image/png;base64,aGVsbG8=",
                      "data:image/png;base64," + base64.b64encode(corrupt_png).decode(),
                      self.payload["screenshot"].replace("image/png", "image/jpeg")]:
            with self.subTest(value=value):
                response = self.client.post("/analyze", json={**self.payload, "screenshot": value})
                self.assertEqual(response.status_code, 400)

    def test_malformed_and_oversized_body(self):
        for body in [b"{", b"[]", b"\xff"]:
            self.assertEqual(self.client.post("/analyze", content=body).status_code, 400)
        response = self.client.post("/analyze", content=b" " * (MAX_REQUEST_BYTES + 1))
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
