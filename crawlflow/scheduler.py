"""可恢复的增量采集调度器。

状态机：
    pending --next()--> running --success()--> done
        ^                  |
        |                  +--failure(network)--> pending（指数退避，next_run_at 到点才可取）
        |                  +--failure(parse)----> skipped（样本记入 parse_samples）
        |                  +--failure(permanent)> dead（死信）
        |                  +--退避次数用尽-------> dead
        +--重启恢复：所有 running 重置为 pending

任务按 (priority 降序, 入队时间 升序) 派发；去重键是归一化 URL 的指纹。
"""
import sqlite3
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import classify_error
from .sites import SiteTracker
from .urlnorm import NormalizeConfig, fingerprint, normalize_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    fingerprint    TEXT PRIMARY KEY,
    url            TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    site           TEXT NOT NULL,
    priority       INTEGER NOT NULL DEFAULT 0,
    attempts       INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'pending',
    next_run_at    REAL NOT NULL DEFAULT 0,
    last_error     TEXT,
    error_kind     TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_dispatch
    ON tasks (status, next_run_at, priority);
CREATE TABLE IF NOT EXISTS results (
    fingerprint TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    body        TEXT,
    fetched_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS parse_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL,
    url         TEXT NOT NULL,
    error       TEXT,
    sample      TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cursors (
    name       TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


@dataclass
class Task:
    url: str
    priority: int = 0
    attempts: int = 0
    fingerprint: str = ""
    site: str = ""
    normalized_url: str = ""


class Scheduler:
    def __init__(self, db_path=":memory:", *, normalize=None, sites=None,
                 default_policy=None, max_attempts=3,
                 backoff_base=1.0, backoff_cap=300.0):
        """db_path 传文件路径即可断点恢复；':memory:' 为纯内存模式。

        max_attempts:  网络/未知错误的最大尝试次数，用尽落死信。
        backoff_base:  指数退避基数（秒），第 n 次失败后等待 base * 2**(n-1)。
        backoff_cap:   退避上限（秒）。
        sites:         {站点名: SitePolicy}，按站点隔离并发与限速。
        """
        self._normalize = normalize or NormalizeConfig()
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        # 崩溃恢复：上次进程退出时处于 running 的任务重新排队
        with self._lock:
            self.conn.execute(
                "UPDATE tasks SET status='pending', next_run_at=0 "
                "WHERE status='running'"
            )
            self.conn.commit()
        self._sites = SiteTracker(sites, default_policy)

    # ---- 入队 ----

    def add(self, url, priority=0, now=None):
        """入队；归一化指纹已存在（含 done/dead）则跳过，返回是否新入队。"""
        now = time.time() if now is None else now
        norm = normalize_url(url, self._normalize)
        fp = fingerprint(url, self._normalize)
        site = urlsplit(norm).hostname or ""
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO tasks "
                "(fingerprint, url, normalized_url, site, priority, attempts,"
                " status, next_run_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 'pending', 0, ?, ?)",
                (fp, url, norm, site, priority, now, now),
            )
            self.conn.commit()
            return cur.rowcount == 1

    # ---- 派发 ----

    def next(self, now=None):
        """取下一个可执行任务：到点、站点有额度，优先级高者优先，同级先入队先出。"""
        now = time.time() if now is None else now
        with self._lock:
            rows = self.conn.execute(
                "SELECT fingerprint, url, normalized_url, site, priority, attempts "
                "FROM tasks WHERE status='pending' AND next_run_at <= ? "
                "ORDER BY priority DESC, rowid ASC LIMIT 100",
                (now,),
            ).fetchall()
            for fp, url, norm, site, priority, attempts in rows:
                if not self._sites.acquire(site, now):
                    continue
                cur = self.conn.execute(
                    "UPDATE tasks SET status='running', updated_at=? "
                    "WHERE fingerprint=? AND status='pending'",
                    (now, fp),
                )
                self.conn.commit()
                if cur.rowcount == 0:  # 极端竞争，理论不会发生在 SQLite 串行写下
                    self._sites.release(site)
                    continue
                return Task(url, priority, attempts, fp, site, norm)
            return None

    # ---- 完成与失败 ----

    def success(self, url, body="", now=None):
        now = time.time() if now is None else now
        fp = fingerprint(url, self._normalize)
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO results (fingerprint, url, body, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (fp, url, body, now),
            )
            cur = self.conn.execute(
                "UPDATE tasks SET status='done', updated_at=? WHERE fingerprint=?",
                (now, fp),
            )
            site = self._site_of(fp)
            self.conn.commit()
        if site:
            self._sites.release(site)

    def failure(self, url, error=None, sample=None, now=None):
        """按错误分类处理失败；error 为 None 时走兼容路径（立即重试，不计退避）。"""
        now = time.time() if now is None else now
        kind = classify_error(error)
        fp = fingerprint(url, self._normalize)
        message = str(error) if error is not None else None
        if isinstance(error, Exception) and hasattr(error, "sample") and error.sample:
            sample = error.sample
        with self._lock:
            row = self.conn.execute(
                "SELECT attempts, site FROM tasks WHERE fingerprint=?", (fp,)
            ).fetchone()
            if row is None:
                return
            attempts, site = row
            attempts += 1
            if kind in ("network", "unknown"):
                if attempts >= self._max_attempts:
                    status, next_run_at = "dead", 0.0
                else:
                    delay = 0.0 if kind == "unknown" else min(
                        self._backoff_base * (2 ** (attempts - 1)),
                        self._backoff_cap,
                    )
                    status, next_run_at = "pending", now + delay
            elif kind == "parse":
                status, next_run_at = "skipped", 0.0
                self.conn.execute(
                    "INSERT INTO parse_samples (fingerprint, url, error, sample, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (fp, url, message, sample, now),
                )
            else:  # permanent
                status, next_run_at = "dead", 0.0
            self.conn.execute(
                "UPDATE tasks SET attempts=?, status=?, next_run_at=?,"
                " last_error=?, error_kind=?, updated_at=? WHERE fingerprint=?",
                (attempts, status, next_run_at, message, kind, now, fp),
            )
            self.conn.commit()
        if site:
            self._sites.release(site)

    # ---- 断点游标 ----

    def set_cursor(self, name, value, now=None):
        """保存断点游标（如某站点已采到的最大 id/时间戳），重启后可读回。"""
        now = time.time() if now is None else now
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO cursors (name, value, updated_at) "
                "VALUES (?, ?, ?)",
                (name, str(value), now),
            )
            self.conn.commit()

    def get_cursor(self, name, default=None):
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM cursors WHERE name=?", (name,)
            ).fetchone()
            return row[0] if row else default

    # ---- 查询 ----

    def pending(self):
        return self._count("pending")

    def running(self):
        return self._sites.running()

    def stats(self):
        with self._lock:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) FROM tasks GROUP BY status"
            ).fetchall()
            return {status: n for status, n in rows}

    def dead_letters(self):
        with self._lock:
            return self.conn.execute(
                "SELECT url, last_error, attempts FROM tasks WHERE status='dead'"
                " ORDER BY updated_at"
            ).fetchall()

    def parse_samples(self):
        with self._lock:
            return self.conn.execute(
                "SELECT url, error, sample FROM parse_samples ORDER BY id"
            ).fetchall()

    def site_retry_after(self, site, now=None):
        return self._sites.retry_after(site, now)

    def close(self):
        with self._lock:
            self.conn.close()

    # ---- 内部 ----

    def _count(self, status):
        with self._lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status=?", (status,)
            ).fetchone()[0]

    def _site_of(self, fp):
        row = self.conn.execute(
            "SELECT site FROM tasks WHERE fingerprint=?", (fp,)
        ).fetchone()
        return row[0] if row else None
