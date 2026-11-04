"""User-managed exact-host overrides; SQLite stores hostnames and decisions only."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from server.domains import DEFAULT_WHITELIST, normalize_hostname

DB_PATH = Path(__file__).resolve().parents[1] / ".data" / "domains.sqlite3"


def hostname(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Expected a domain or HTTP(S) URL")
    return normalize_hostname(value if "://" in value else "https://" + value)


@contextmanager
def database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=2)
    try:
        with connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS domain_policies (
                hostname TEXT PRIMARY KEY,
                policy TEXT NOT NULL CHECK (policy IN ('whitelist', 'blacklist'))
            )""")
            connection.execute("CREATE TABLE IF NOT EXISTS policy_settings (key TEXT PRIMARY KEY)")
            if not connection.execute("SELECT 1 FROM policy_settings WHERE key = 'initial_whitelist_v1'").fetchone():
                # Seed once; subsequent removals and blacklist decisions must persist.
                connection.executemany("INSERT OR IGNORE INTO domain_policies (hostname, policy) VALUES (?, 'whitelist')",
                                       [(host,) for host in sorted(DEFAULT_WHITELIST)])
                connection.execute("INSERT OR IGNORE INTO policy_settings (key) VALUES ('initial_whitelist_v1')")
            yield connection
    finally:
        connection.close()


def lookup(host):
    with database() as connection:
        row = connection.execute("SELECT policy FROM domain_policies WHERE hostname = ?", (host,)).fetchone()
    return row[0] if row else None


def save(value, policy):
    if policy not in {"whitelist", "blacklist"}:
        raise ValueError("Expected whitelist or blacklist")
    host = hostname(value)
    with database() as connection:
        connection.execute("""INSERT INTO domain_policies (hostname, policy) VALUES (?, ?)
            ON CONFLICT(hostname) DO UPDATE SET policy = excluded.policy""", (host, policy))
    return host


def remove(value):
    host = hostname(value)
    with database() as connection:
        connection.execute("DELETE FROM domain_policies WHERE hostname = ?", (host,))
    return host


def entries():
    with database() as connection:
        rows = connection.execute("SELECT hostname, policy FROM domain_policies ORDER BY hostname").fetchall()
    return [{"hostname": host, "policy": policy} for host, policy in rows]


def verdict(host, policy):
    return {"hostname": host, "policy": policy, "source": "user_list", "brand": None,
            "verdict": "safe" if policy == "whitelist" else "dangerous",
            "reason": "This domain is on your blacklist. You marked it as dangerous."
                      if policy == "blacklist" else "Domain is on your whitelist; model analysis skipped"}
