import unittest

from server.domains import APPROVED_HOSTS, normalize_hostname, verify_domain


class DomainTests(unittest.TestCase):
    def test_each_explicit_host_and_normalization(self):
        for brand, hosts in APPROVED_HOSTS.items():
            for host in hosts:
                with self.subTest(brand=brand, host=host):
                    self.assertEqual(verify_domain(brand, f"https://{host}/login"), "safe")
                    self.assertEqual(verify_domain(brand, f"https://{host.upper()}.:443/login?q=1#x"), "safe")

    def test_lookalikes_suffixes_userinfo_and_unapproved_subdomains(self):
        for url in [
            "https://login.microsoftonline.com.attacker.example/",
            "https://attacker-login.microsoftonline.com/",
            "https://child.login.microsoftonline.com/",
            "https://login.microsoftonline.com@attacker.example/",
            "https://attacker.example/#https://login.microsoftonline.com",
            "https://attacker.example/?next=https://login.microsoftonline.com",
            "https://login.micr0softonline.com/",
            "https://login.mіcrosoftonline.com/",  # Cyrillic i, converted to punycode.
            "https://127.0.0.1/", "https://[::1]/", "https://microsoft.com/",
        ]:
            with self.subTest(url=url):
                self.assertEqual(verify_domain("microsoft", url), "dangerous")

    def test_brand_specific_and_unknown_brands(self):
        self.assertEqual(verify_domain("google", "https://login.live.com"), "dangerous")
        self.assertEqual(verify_domain("microsoft", "https://accounts.google.com"), "dangerous")
        for brand in [None, "unknown", "unrecognised"]:
            self.assertEqual(verify_domain(brand, "https://accounts.google.com"), "unknown")

    def test_malformed_urls_never_pass(self):
        for url in [None, "", "file:///login", "javascript:alert(1)", "https://",
                    "https://login.microsoftonline.com:bad/", "https://host:99999/",
                    "https://login.micro\nsoftonline.com/", " https://login.live.com/",
                    "https://login.live.com\\@attacker.example/", "https://login.live.com../",
                    "https://%6cogin.live.com/", "https://[broken/", "https://-host/",
                    "https://bad_host/", "https://host..example/", "https://host\x00/",
                    "https://login.micro\u200csoftonline.com/"]:
            with self.subTest(url=url):
                with self.assertRaises((ValueError, UnicodeError)):
                    normalize_hostname(url)
                self.assertEqual(verify_domain("microsoft", url), "unknown")


if __name__ == "__main__":
    unittest.main()
