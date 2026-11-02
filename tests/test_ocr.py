import io
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image
from server.ocr import check_login, is_login_text, load_reader


class LoginRuleTests(unittest.TestCase):
    def test_ocr_bounds_resolution_preserves_aspect_and_batches_boxes(self):
        for source_size, expected_size in [((2560, 1440), (1280, 720)),
                                           ((1000, 2000), (360, 720)),
                                           ((455, 506), (455, 506))]:
            with self.subTest(source_size=source_size):
                data = io.BytesIO()
                Image.new("RGB", source_size, "red").save(data, format="PNG")
                reader = MagicMock()
                reader.readtext.return_value = ["Sign in"]
                with patch.dict("sys.modules", {"torch": MagicMock()}):
                    detected, elapsed = check_login(reader, data.getvalue())
                self.assertTrue(detected)
                self.assertGreaterEqual(elapsed, 0)
                pixels = reader.readtext.call_args.args[0]
                self.assertIsInstance(pixels, np.ndarray)
                self.assertEqual(pixels.shape, (expected_size[1], expected_size[0], 3))
                self.assertEqual(pixels[0, 0].tolist(), [0, 0, 255])
                self.assertEqual(reader.readtext.call_args.kwargs["batch_size"], 16)

    def test_typical_login_text(self):
        for parts in [["SIGN", "IN"], ["Log in to your account"], ["Login"],
                      ["Email address", "Password", "Continue"], ["Username", "Password"],
                      ["  Sign\n\t in  ", "Continue"]]:
            with self.subTest(parts=parts):
                self.assertTrue(is_login_text(parts))

    def test_ordinary_text_and_word_boundaries(self):
        for parts in [[], ["Latest news"], ["Contact us by email"], ["Email address", "Subscribe"],
                      ["Password safety tips"], ["Username"], ["Design inspiration"], ["loginology"]]:
            with self.subTest(parts=parts):
                self.assertFalse(is_login_text(parts))

    def test_cuda_unavailable_fails_before_reader_creation(self):
        torch = MagicMock()
        torch.cuda.is_available.return_value = False
        easyocr = MagicMock()
        with patch.dict("sys.modules", {"torch": torch, "easyocr": easyocr}):
            with self.assertRaisesRegex(RuntimeError, "CUDA is required"):
                load_reader()
        easyocr.Reader.assert_not_called()

    def test_cpu_fallback_is_rejected(self):
        torch = MagicMock()
        easyocr = MagicMock()
        easyocr.Reader.return_value.device = "cpu"
        with patch.dict("sys.modules", {"torch": torch, "easyocr": easyocr}):
            with self.assertRaisesRegex(RuntimeError, "CPU fallback is disabled"):
                load_reader()
        self.assertTrue(easyocr.Reader.call_args.kwargs["gpu"])


if __name__ == "__main__":
    unittest.main()
