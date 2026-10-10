"""Small durable store: policies, first-seen image digests, and update recovery plans."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_POLICY = {"checkCron": "0 */6 * * *", "updateMode": "scheduled",
                  "updateCron": "0 3 * * *", "waitValue": 24, "waitUnit": "hours", "auto": []}


def normalize_policy(policy):
    policy = dict(policy)
    if policy.get("waitValue") is None:
        policy["waitValue"] = policy.get("waitHours") if policy.get("waitHours") is not None else 24
        policy["waitUnit"] = "hours"
    policy.pop("waitHours", None)
    return policy


def wait_seconds(policy):
    policy = normalize_policy(policy)
    return policy["waitValue"] * (60 if policy.get("waitUnit") == "minutes" else 3600)


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS images (name TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.commit()

    def get(self, table, key, default=None):
        column = "key" if table == "settings" else "name" if table == "images" else "id"
        with self.lock:
            row = self.db.execute(f"SELECT value FROM {table} WHERE {column}=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, table, key, value):
        column = "key" if table == "settings" else "name" if table == "images" else "id"
        with self.lock, self.db:
            self.db.execute(f"INSERT OR REPLACE INTO {table} ({column},value) VALUES (?,?)",
                            (key, json.dumps(value, ensure_ascii=False)))

    def all(self, table):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute(f"SELECT value FROM {table}")]

    def policy(self):
        return {**DEFAULT_POLICY, **normalize_policy(self.get("settings", "policy", {}))}

    def discovery(self, name, image_id, digest, available, now=None):
        now = now or time.time()
        old = self.get("images", name, {})
        # A new target digest has its own waiting period; repeated checks do not reset it.
        first = old.get("discoveredAt") if old.get("digest") == digest and old.get("imageId") == image_id else None
        value = {"name": name, "imageId": image_id, "digest": digest,
                 "status": "available" if available else "current", "checkedAt": now,
                 "discoveredAt": (first or now) if available else None, "error": None}
        self.put("images", name, value)
        return value
