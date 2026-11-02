"""Small static login-host map. Entries are exact hosts, never suffix patterns."""

import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import idna

# Shared with the extension so recognition and UI validation cannot drift.
BRAND_CONFIG = json.loads((Path(__file__).resolve().parents[1] / "extension" / "brands.json").read_text(encoding="utf-8"))
APPROVED_HOSTS = {brand: set(info["hosts"]) for brand, info in BRAND_CONFIG.items()}
DEFAULT_WHITELIST = {info["primary"] for info in BRAND_CONFIG.values() if info["primary"]}


def normalize_hostname(url):
    if (not isinstance(url, str) or len(url) > 16_384 or "\\" in url
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)):
        raise ValueError("Invalid HTTP(S) URL")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Invalid HTTP(S) URL")
    _ = parsed.port  # Validates the port without using it as a trust signal.
    host = parsed.hostname.lower()
    if host.endswith("."):
        host = host[:-1]  # A single DNS root dot is equivalent to the same host.
    if "%" in host:
        raise ValueError("Encoded hosts and zone IDs are unsupported")
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    # UTS #46 validation avoids Python's legacy IDNA-2003 character folding.
    host = idna.encode(host, uts46=True, std3_rules=True).decode("ascii")
    if len(host) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in host.split(".")
    ):
        raise ValueError("Invalid hostname")
    return host


def verify_domain(brand, url):
    if not isinstance(brand, str) or brand not in APPROVED_HOSTS:
        return "unknown"
    try:
        host = normalize_hostname(url)
    except (ValueError, UnicodeError):
        return "unknown"
    return "safe" if host in APPROVED_HOSTS[brand] else "dangerous"
