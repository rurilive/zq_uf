"""A tiny incremental crawl scheduler used by the data pipeline baseline."""
import sqlite3
from dataclasses import dataclass


@dataclass
class Task:
    url: str
    priority: int = 0
    attempts: int = 0


class Scheduler:
    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            "url TEXT PRIMARY KEY, priority INTEGER, attempts INTEGER, status TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS results (url TEXT PRIMARY KEY, body TEXT)"
        )
        self.conn.commit()

    def add(self, url: str, priority: int = 0) -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO tasks (url, priority, attempts, status) VALUES (?, ?, 0, 'pending')",
            (url, priority),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def next(self):
        row = self.conn.execute(
            "SELECT url, priority, attempts FROM tasks "
            "WHERE status='pending' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if not row:
            return None
        self.conn.execute("UPDATE tasks SET status='running' WHERE url=?", (row[0],))
        self.conn.commit()
        return Task(row[0], row[1], row[2])

    def success(self, url: str, body: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO results (url, body) VALUES (?, ?)", (url, body)
        )
        self.conn.execute("UPDATE tasks SET status='done' WHERE url=?", (url,))
        self.conn.commit()

    def failure(self, url: str):
        row = self.conn.execute(
            "SELECT attempts FROM tasks WHERE url=?", (url,)
        ).fetchone()
        attempts = (row[0] if row else 0) + 1
        status = "failed" if attempts >= 3 else "pending"
        self.conn.execute(
            "UPDATE tasks SET attempts=?, status=? WHERE url=?", (attempts, status, url)
        )
        self.conn.commit()

    def pending(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='pending'"
        ).fetchone()[0]
