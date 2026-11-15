"""账号级登录态存储。

同一份登录态要在多台机器之间共享，管理器本身不持有状态，全部落到
:class:`SessionRecord` 并由 :class:`Store` 持久化：

- :class:`InMemoryStore`：进程内共享，线程安全，适合单进程或测试；
- :class:`SqliteStore`：状态写入一个 SQLite 文件，多进程/多机挂载同一
  文件即可拿到同一份登录态。跨进程“同一时刻只允许一个登录”靠
  ``BEGIN IMMEDIATE`` 写事务 + login_locks 表实现单飞（single-flight）。

生产环境如果已有 Redis/MySQL，按 :class:`Store` 的接口再实现一个即可，
管理器代码不用改。
"""

import contextlib
import json
import sqlite3
import threading
from dataclasses import asdict, dataclass

# 账号状态机
HEALTHY = "HEALTHY"  # 正常采集
PAUSED = "PAUSED"    # 失败冷却，暂停期间不发起新的登录
TRIAL = "TRIAL"      # 恢复试探期，小流量登录，连续成功后恢复正常


@dataclass
class SessionRecord:
    """一个账号的完整登录态与熔断状态。"""

    account: str
    state: str = HEALTHY
    # 当前令牌
    token: str = None
    token_issue_at: float = 0.0
    token_expires_at: float = 0.0
    renewable: bool = True
    # 履历标记，会透传到 lease/refresh 返回里
    ever_refreshed: bool = False
    ever_relogin: bool = False
    # 熔断/试探
    fail_count: int = 0
    consecutive_success: int = 0
    paused_until: float = 0.0
    pause_reason: str = None
    pause_kind: str = None  # account / site
    # 统计
    lease_count: int = 0
    updated_at: float = 0.0

    def ttl(self, now):
        """剩余可用秒数，不会是负数。"""
        return max(0.0, self.token_expires_at - now)

    def is_paused(self, now):
        return self.state == PAUSED and self.paused_until > now

    def is_expired(self, now):
        return not self.token or now >= self.token_expires_at

    def needs_renew(self, now, margin):
        """到期前 margin 秒内且仍可静默续期。"""
        return (
            bool(self.token)
            and self.renewable
            and not self.is_expired(now)
            and self.ttl(now) <= margin
        )

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class Store:
    """存储接口。换 Redis/MySQL 时实现这几个方法即可。"""

    def get(self, account):
        raise NotImplementedError

    def put(self, record):
        """写入会话（token 轮换时同时登记 token -> account 索引）。"""
        raise NotImplementedError

    def lookup_token(self, token):
        """按令牌反查账号，支持刷新旧令牌。"""
        raise NotImplementedError

    @contextlib.contextmanager
    def lock_account(self, account):
        """进程内的账号锁，串行化同账号的并发请求。"""
        raise NotImplementedError

    def try_acquire_login(self, account, owner, lock_ttl, now):
        """尝试获取跨进程登录单飞锁，拿到返回 True。

        已被其他持有者占用且未过期返回 False；锁带 TTL，持有者崩溃后
        不会永久卡死。
        """
        raise NotImplementedError

    def release_login(self, account, owner):
        raise NotImplementedError

    def version_of(self, account):
        """会话当前版本号，写入一次加一，用于等待别的进程更新。"""
        raise NotImplementedError

    def close(self):
        pass


class InMemoryStore(Store):
    """进程内存储，线程安全。"""

    def __init__(self):
        self._sessions = {}
        self._versions = {}
        self._tokens = {}
        self._locks = {}
        self._login_locks = {}
        self._guard = threading.RLock()

    def get(self, account):
        with self._guard:
            rec = self._sessions.get(account)
            return SessionRecord.from_dict(rec.to_dict()) if rec else None

    def put(self, record):
        with self._guard:
            record.updated_at = record.updated_at or 0.0
            self._sessions[record.account] = record
            self._versions[record.account] = self._versions.get(record.account, 0) + 1
            if record.token:
                self._tokens[record.token] = record.account

    def lookup_token(self, token):
        with self._guard:
            return self._tokens.get(token)

    @contextlib.contextmanager
    def lock_account(self, account):
        with self._guard:
            lock = self._locks.get(account)
            if lock is None:
                lock = threading.Lock()
                self._locks[account] = lock
        with lock:
            yield

    def try_acquire_login(self, account, owner, lock_ttl, now):
        with self._guard:
            held = self._login_locks.get(account)
            if held and held[1] > now:
                return False
            self._login_locks[account] = (owner, now + lock_ttl)
            return True

    def release_login(self, account, owner):
        with self._guard:
            held = self._login_locks.get(account)
            if held and held[0] == owner:
                self._login_locks.pop(account, None)

    def version_of(self, account):
        with self._guard:
            return self._versions.get(account, 0)


class SqliteStore(Store):
    """SQLite 持久化存储，支持多进程共享同一文件。

    每个 Store 实例持有一个长连接，进程内用 RLock 串行化访问；
    跨进程通过 WAL + busy_timeout 容忍锁等待，登录单飞用
    login_locks 表 + IMMEDIATE 事务保证。
    """

    def __init__(self, path, lock_ttl=60.0):
        self._path = path
        self._lock_ttl = lock_ttl
        self._guard = threading.RLock()
        self._local_locks = {}
        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None, timeout=30.0
        )
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # 多进程并发首次建库时，WAL 切换需要排他锁，重试即可
        self._init_with_retry()

    def _init_with_retry(self, attempts=20, delay=0.1):
        import time

        last_err = None
        for i in range(attempts):
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._create_tables()
                return
            except sqlite3.OperationalError as exc:
                last_err = exc
                time.sleep(delay)
        raise last_err

    def _create_tables(self):
        with self._guard:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    account TEXT PRIMARY KEY,
                    data    TEXT NOT NULL,
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    token   TEXT PRIMARY KEY,
                    account TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_locks (
                    account    TEXT PRIMARY KEY,
                    owner      TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                """
            )

    def get(self, account):
        with self._guard:
            row = self._conn.execute(
                "SELECT data FROM sessions WHERE account = ?", (account,)
            ).fetchone()
            return SessionRecord.from_dict(json.loads(row[0])) if row else None

    def put(self, record):
        payload = json.dumps(record.to_dict(), ensure_ascii=False)
        with self._guard:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO sessions(account, data, version)
                    VALUES (?, ?, 1)
                    ON CONFLICT(account) DO UPDATE SET
                        data = excluded.data,
                        version = sessions.version + 1
                    """,
                    (record.account, payload),
                )
                if record.token:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO tokens(token, account) VALUES (?, ?)",
                        (record.token, record.account),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def lookup_token(self, token):
        with self._guard:
            row = self._conn.execute(
                "SELECT account FROM tokens WHERE token = ?", (token,)
            ).fetchone()
            return row[0] if row else None

    @contextlib.contextmanager
    def lock_account(self, account):
        with self._guard:
            lock = self._local_locks.get(account)
            if lock is None:
                lock = threading.Lock()
                self._local_locks[account] = lock
        with lock:
            yield

    def try_acquire_login(self, account, owner, lock_ttl, now):
        with self._guard:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT owner, expires_at FROM login_locks WHERE account = ?",
                    (account,),
                ).fetchone()
                if row and row[1] > now:
                    self._conn.execute("COMMIT")
                    return False
                self._conn.execute(
                    """
                    INSERT INTO login_locks(account, owner, expires_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(account) DO UPDATE SET
                        owner = excluded.owner,
                        expires_at = excluded.expires_at
                    """,
                    (account, owner, now + lock_ttl),
                )
                self._conn.execute("COMMIT")
                return True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def release_login(self, account, owner):
        with self._guard:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "DELETE FROM login_locks WHERE account = ? AND owner = ?",
                    (account, owner),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def version_of(self, account):
        with self._guard:
            row = self._conn.execute(
                "SELECT version FROM sessions WHERE account = ?", (account,)
            ).fetchone()
            return row[0] if row else 0

    def close(self):
        with self._guard:
            self._conn.close()


def open_store(path=None):
    """path 为 None 用内存存储，否则打开/创建 SQLite 文件。"""
    if path is None:
        return InMemoryStore()
    return SqliteStore(path)
