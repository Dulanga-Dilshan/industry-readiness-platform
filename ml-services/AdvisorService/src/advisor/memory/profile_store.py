"""Profile store (SQLite on the advisor volume). The backend owns the truth and re-pushes; this is a cache."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone


class ProfileStore:
    def __init__(self, path: str = ":memory:"):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS profiles(user_id TEXT PRIMARY KEY, body TEXT, updated_at TEXT)")
        self.db.commit()

    def put(self, profile: dict) -> None:
        uid = profile["user_id"]
        profile = {**profile, "updated_at": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO profiles VALUES(?,?,?)", (uid, json.dumps(profile), profile["updated_at"]))
            self.db.commit()

    def get(self, user_id: str) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT body FROM profiles WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def delete(self, user_id: str) -> bool:
        with self._lock:
            cur = self.db.execute("DELETE FROM profiles WHERE user_id=?", (user_id,))
            self.db.commit()
            return cur.rowcount > 0
