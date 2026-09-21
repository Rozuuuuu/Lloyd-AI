import sqlite3, os
from datetime import datetime

class JarvisMemory:
    def __init__(self, db_path="data/jarvis_memory.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._migrate()

    def _migrate(self):
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT, role TEXT, content TEXT, "
            "timestamp TEXT DEFAULT CURRENT_TIMESTAMP)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS facts ("
            "key TEXT PRIMARY KEY, value TEXT, "
            "category TEXT DEFAULT 'general', "
            "updated_at TEXT)")

        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(conversations)").fetchall()]
        if "session_id" not in cols:
            self.conn.execute("ALTER TABLE conversations ADD COLUMN session_id TEXT DEFAULT 'legacy'")

        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(facts)").fetchall()]
        if "category" not in cols:
            self.conn.execute("ALTER TABLE facts ADD COLUMN category TEXT DEFAULT 'general'")

        self.conn.commit()

    # ---------- conversations ----------
    def save_message(self, session_id, role, content):
        self.conn.execute(
            "INSERT INTO conversations (session_id, role, content) VALUES (?,?,?)",
            (session_id, role, content))
        self.conn.commit()

    def get_recent_context(self, session_id, limit=10):
        rows = self.conn.execute(
            "SELECT role, content FROM conversations "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit)).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    # ---------- user facts ----------
    def remember_fact(self, key, value, category="general"):
        key = str(key).strip().lower()[:80]
        value = str(value).strip()[:500]
        self.conn.execute(
            "INSERT OR REPLACE INTO facts (key, value, category, updated_at) "
            "VALUES (?,?,?,?)",
            (key, value, category, datetime.now().isoformat()))
        self.conn.commit()

    def forget_fact(self, key):
        self.conn.execute("DELETE FROM facts WHERE key = ?",
                          (str(key).strip().lower(),))
        self.conn.commit()

    def facts_summary(self):
        rows = self.conn.execute(
            "SELECT key, value, category FROM facts "
            "ORDER BY category, key").fetchall()
        if not rows:
            return ""
        by_cat = {}
        for k, v, c in rows:
            by_cat.setdefault(c or "general", []).append((k, v))
        lines = []
        for cat, items in by_cat.items():
            lines.append(f"[{cat}]")
            for k, v in items:
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)