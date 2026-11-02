from pathlib import Path
from contextlib import closing
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from server.main import app
from server import policies

HEADERS = {"X-NoPhish-Action": "domain-policy", "Origin": "chrome-extension://test"}


class DomainPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "domains.sqlite3"
        for name, value in [("server.policies.DB_PATH", self.path),
                            ("server.main.load_reader", None), ("server.main.load_classifier", None)]:
            item = patch(name, value) if name.endswith("DB_PATH") else patch(name, return_value=value)
            item.start()
            self.addCleanup(item.stop)
        for name in ["check_login", "classify_brand"]:
            item = patch("server.main." + name)
            setattr(self, name, item.start())
            self.addCleanup(item.stop)
        self.client = TestClient(app).__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def save(self, domain, policy):
        response = self.client.put("/domain-policies", json={"domain": domain, "policy": policy}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_list_decisions_bypass_image_decode_and_both_models(self):
        for policy, verdict in [("whitelist", "safe"), ("blacklist", "dangerous")]:
            self.save("Example.COM.", policy)
            # No screenshot, no available models: an exact-host override still resolves.
            response = self.client.post("/analyze", json={"url": "https://example.com/path?secret=not-stored"})
            self.assertEqual(response.status_code, 200)
            result = response.json()
            self.assertEqual((result["verdict"], result["brand"], result["source"]), (verdict, None, "user_list"))
        self.check_login.assert_not_called()
        self.classify_brand.assert_not_called()

    def test_replace_remove_list_and_persistence(self):
        self.save("https://EXAMPLE.com/account?private=data", "whitelist")
        self.save("example.com", "blacklist")
        self.assertEqual(self.client.get("/domain-policies").json()["entries"],
                         [{"hostname": "example.com", "policy": "blacklist"}])
        # Every operation opens a new connection, verifying on-disk persistence.
        self.assertEqual(policies.lookup("example.com"), "blacklist")
        with closing(sqlite3.connect(self.path)) as database:
            self.assertEqual(database.execute("SELECT * FROM domain_policies").fetchall(), [("example.com", "blacklist")])
        self.assertEqual(self.client.request("DELETE", "/domain-policies", json={"domain": "example.com"}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.get("/domain-policies").json()["entries"], [])
        self.assertIsNone(policies.lookup("example.com"))

    def test_exact_matching_and_idn_normalization(self):
        self.save("https://BÜCHER.example./login", "whitelist")
        self.assertEqual(policies.lookup("xn--bcher-kva.example"), "whitelist")
        self.save("example.com", "whitelist")
        for host in ["www.example.com", "example.com.attacker.invalid", "notexample.com"]:
            self.assertIsNone(policies.lookup(host))

    def test_unlisted_or_removed_domains_follow_normal_pipeline(self):
        self.save("example.com", "whitelist")
        self.client.request("DELETE", "/domain-policies", json={"domain": "example.com"}, headers=HEADERS)
        self.assertEqual(self.client.post("/analyze", json={"url": "https://example.com/"}).status_code, 400)
        self.check_login.assert_not_called()  # Standard missing-screenshot validation resumes.

    def test_invalid_input_and_browser_origin_cannot_modify_lists(self):
        for payload in [{"domain": "example.com", "policy": "both"}, {"domain": "example.com", "policy": []},
                        {"domain": "https://bad_host/", "policy": "whitelist"}, {"domain": None, "policy": "blacklist"}]:
            self.assertEqual(self.client.put("/domain-policies", json=payload, headers=HEADERS).status_code, 400)
        for headers in [{}, {"X-NoPhish-Action": "domain-policy", "Origin": "https://attacker.invalid"}]:
            self.assertEqual(self.client.put("/domain-policies", json={"domain": "example.com", "policy": "whitelist"}, headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/domain-policies").json()["entries"], [])

    def test_database_errors_are_inconclusive_without_inference(self):
        with patch("server.main.policies.lookup", side_effect=sqlite3.OperationalError("private details")):
            response = self.client.post("/analyze", json={"url": "https://example.com/", "screenshot": "invalid"})
        self.assertEqual(response.json()["verdict"], "unknown")
        self.assertNotIn("private details", response.text)
        self.check_login.assert_not_called()
        self.classify_brand.assert_not_called()


if __name__ == "__main__":
    unittest.main()
