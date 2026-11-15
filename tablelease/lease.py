"""数据表写租约。

用 SQLite 持久化租约状态，解决进程内集合方案的三个问题：

1. 同一张表同一时刻只有一个写者，后到者进入 FIFO 等待队列；
2. 租约带到期时间，持有者进程崩溃后到期可被他人接管，并写审计；
3. 同一持有者重入安全（引用计数），释放幂等，不会自己把自己锁死。

每次发放新租约（含接管）都会产生一个更大的 fencing token，
配合 :class:`tablelease.fencing.FencedWriter` 可阻止过期持有者继续写表，
保证接管后旧进程的迟到写入不会覆盖新持有者的数据。
"""

import json
import sqlite3
import time
import uuid

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
    table_name  TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    expires_at  REAL NOT NULL,
    entry_count INTEGER NOT NULL,
    fence_token INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS waiters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name  TEXT NOT NULL,
    owner       TEXT NOT NULL,
    enqueued_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_waiters_table ON waiters(table_name, id);
CREATE TABLE IF NOT EXISTS lease_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    table_name  TEXT NOT NULL,
    event       TEXT NOT NULL,
    owner       TEXT,
    detail      TEXT
);
"""

# 模块级默认配置；configure() 可替换。测试用独立库和可控时钟。
_DB_PATH = None
_CLOCK = time.time
_DEFAULT_WAITER_TTL = 300.0


class LeaseError(Exception):
    """租约操作的业务错误（如续约时持有者不匹配）。"""


def configure(db_path=None, clock=None):
    """配置租约库路径和时钟函数（测试用），返回当前配置。"""
    global _DB_PATH, _CLOCK
    if db_path is not None:
        _DB_PATH = db_path
    if clock is not None:
        _CLOCK = clock
    return {"db_path": _DB_PATH, "clock": _CLOCK}


def _connect():
    if _DB_PATH is None:
        raise LeaseError("租约库未配置，请先调用 tablelease.configure(db_path=...)")
    # 每个操作独立连接，autocommit 让事务边界完全由 BEGIN/COMMIT 显式控制，
    # 配合 WAL 与 busy_timeout，多进程下写操作由 SQLite 串行化。
    # busy_timeout 必须先于一切可能拿锁的语句（含 WAL 切换）设置。
    conn = sqlite3.connect(_DB_PATH, timeout=30, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db():
    conn = _connect()
    try:
        # WAL 模式只在建库时切换一次；切换需要排他锁，靠 busy_timeout 等待。
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
    finally:
        conn.close()


class _transaction:
    """显式事务上下文（autocommit 连接上的 BEGIN IMMEDIATE 立即拿写锁）。"""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False


def _audit(conn, table, event, owner=None, **detail):
    conn.execute(
        "INSERT INTO lease_audit(ts, table_name, event, owner, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (_CLOCK(), table, event, owner,
         json.dumps(detail, ensure_ascii=False) if detail else None),
    )


def _purge_stale_waiters(conn, table, now):
    """清理等待队列里自己也崩溃了的等待者（超过 waiter_ttl 未刷新）。"""
    conn.execute(
        "DELETE FROM waiters WHERE table_name=? AND enqueued_at < ?",
        (table, now - _DEFAULT_WAITER_TTL),
    )


def _wait_position(conn, table, waiter_id):
    row = conn.execute(
        "SELECT COUNT(*) FROM waiters WHERE table_name=? AND id<=?",
        (table, waiter_id),
    ).fetchone()
    return row[0]


def _try_acquire(conn, table, owner, ttl, now):
    """在已持写锁的事务内尝试获取，返回响应 dict 或 None（需要排队）。"""
    _purge_stale_waiters(conn, table, now)
    row = conn.execute(
        "SELECT owner, expires_at, entry_count, fence_token "
        "FROM leases WHERE table_name=?",
        (table,),
    ).fetchone()
    current_holder = row[0] if row is not None else None

    # FIFO 公平性在服务端强制：只要该 owner 前面还有等待者，它就不能
    # 获取——哪怕租约恰好空闲或刚过期，也必须等队头先走。
    # 例外：当前持有者重入永远放行，不能被队列里的旧记录自锁。
    if current_holder != owner:
        head_waiter = conn.execute(
            "SELECT owner FROM waiters WHERE table_name=? "
            "ORDER BY id LIMIT 1",
            (table,),
        ).fetchone()
        if head_waiter is not None and head_waiter[0] != owner:
            return None



    if row is None:
        token = _next_fence(conn, table)
        conn.execute(
            "INSERT INTO leases(table_name, owner, expires_at, entry_count, fence_token) "
            "VALUES (?, ?, ?, 1, ?)",
            (table, owner, now + ttl, token),
        )
        conn.execute(
            "DELETE FROM waiters WHERE table_name=? AND owner=?",
            (table, owner),
        )
        _audit(conn, table, "granted", owner, ttl=ttl, fence_token=token)
        return _granted_response(table, owner, now + ttl, now, 1, token,
                                 False, None)

    expires_at, entry_count, token = row[1], row[2], row[3]

    if current_holder == owner:
        # 重入：同一持有者重复进入写流程，只增加计数，绝不自锁。
        new_count = entry_count + 1
        conn.execute(
            "UPDATE leases SET expires_at=?, entry_count=? "
            "WHERE table_name=?",
            (now + ttl, new_count, table),
        )
        conn.execute(
            "DELETE FROM waiters WHERE table_name=? AND owner=?",
            (table, owner),
        )
        _audit(conn, table, "reentrant", owner, entry_count=new_count)
        return _granted_response(table, owner, now + ttl, now, new_count,
                                 token, False, None)

    if now >= expires_at:
        # 原持有者已过期（典型场景：进程崩溃），接管。
        new_token = _next_fence(conn, table)
        conn.execute(
            "UPDATE leases SET owner=?, expires_at=?, entry_count=1, "
            "fence_token=? WHERE table_name=?",
            (owner, now + ttl, new_token, table),
        )
        conn.execute(
            "DELETE FROM waiters WHERE table_name=? AND owner=?",
            (table, owner),
        )
        _audit(conn, table, "takeover", owner,
               previous_owner=current_holder,
               expired_at=expires_at,
               fence_token=new_token)
        return _granted_response(table, owner, now + ttl, now, 1, new_token,
                                 True, current_holder)

    return None


def _next_fence(conn, table):
    """fencing token 全库单调递增，同表每次新发放严格变大。"""
    row = conn.execute(
        "SELECT COALESCE(MAX(fence_token), 0) FROM leases WHERE table_name=?",
        (table,),
    ).fetchone()
    return row[0] + 1


def _granted_response(table, owner, expires_at, now, entry_count,
                      fence_token, taken_over, previous_owner):
    return {
        "table": table,
        "owner": owner,
        "holder": owner,
        "granted": True,
        "expires_in": max(0.0, expires_at - now),
        "entry_count": entry_count,
        "fence_token": fence_token,
        "taken_over": taken_over,
        "previous_owner": previous_owner,
        "wait_position": 0,
    }


def acquire(table, owner, ttl, block=False, timeout=None, poll_interval=0.05):
    """获取表写租约。

    - 表空闲、自己持有（重入）、或当前持有者已过期（接管）时立即发放；
    - 被他人持有时，非阻塞模式（默认）入队并返回 ``granted=False`` 和
      等待位置；阻塞模式按 FIFO 等待，直到获取成功或 ``timeout`` 到期；
    - 等待位置按到达顺序，队头未离开时后续等待者不会越过。

    返回包含：持有者、剩余时间（秒）、等待位置、是否接管、fencing token。
    """
    if not table or not owner:
        raise ValueError("table 和 owner 必须是非空字符串")
    if ttl <= 0:
        raise ValueError("ttl 必须为正数")
    if timeout is not None and timeout < 0:
        raise ValueError("timeout 不能为负数")

    _init_db()
    deadline = None if timeout is None else _CLOCK() + timeout

    while True:
        conn = _connect()
        try:
            with _transaction(conn):
                now = _CLOCK()
                result = _try_acquire(conn, table, owner, ttl, now)
                if result is not None:
                    return result

                # 被他人有效持有：同一等待者只保留一条排队记录。
                existing = conn.execute(
                    "SELECT id FROM waiters WHERE table_name=? AND owner=?",
                    (table, owner),
                ).fetchone()
                if existing is None:
                    cur = conn.execute(
                        "INSERT INTO waiters(table_name, owner, enqueued_at) "
                        "VALUES (?, ?, ?)",
                        (table, owner, now),
                    )
                    waiter_id = cur.lastrowid
                    _audit(conn, table, "wait", owner)
                else:
                    waiter_id = existing[0]
                    # 刷新排队心跳，避免活跃等待者被当成崩溃等待者清理。
                    conn.execute(
                        "UPDATE waiters SET enqueued_at=? WHERE id=?",
                        (now, waiter_id),
                    )
                position = _wait_position(conn, table, waiter_id)
                lease_row = conn.execute(
                    "SELECT owner, expires_at FROM leases WHERE table_name=?",
                    (table,),
                ).fetchone()
                # 队头阻挡时租约可能正处于"已释放、下一位尚未接手"的
                # 空档，lease_row 为 None，此时剩余时间记 0。
                if lease_row is None:
                    holder, expires_in = None, 0.0
                else:
                    holder, expires_at = lease_row
                    expires_in = max(0.0, expires_at - now)
                response = {
                    "table": table,
                    "owner": owner,
                    "holder": holder,
                    "granted": False,
                    "expires_in": expires_in,
                    "entry_count": 0,
                    "fence_token": None,
                    "taken_over": False,
                    "previous_owner": None,
                    "wait_position": position,
                }
        finally:
            conn.close()

        if not block:
            return response
        if deadline is not None and _CLOCK() >= deadline:
            _leave_queue(table, owner)
            response["timed_out"] = True
            return response
        time.sleep(poll_interval)


def _leave_queue(table, owner):
    conn = _connect()
    try:
        with _transaction(conn):
            conn.execute(
                "DELETE FROM waiters WHERE table_name=? AND owner=?",
                (table, owner),
            )
    finally:
        conn.close()


def release(table, owner):
    """释放租约，幂等且不报错。

    - 持有者匹配：引用计数减一，归零才真正释放，并唤醒队头；
    - 非持有者释放、释放不存在的租约、重复释放：均返回
      ``released=False`` 且不抛异常，重复释放 ``duplicate=True``；
    - 发现持有者已过期时先记 takeover 审计再按无租约处理。
    """
    if not table or not owner:
        raise ValueError("table 和 owner 必须是非空字符串")

    _init_db()
    conn = _connect()
    try:
        with _transaction(conn):
            now = _CLOCK()
            row = conn.execute(
                "SELECT owner, expires_at, entry_count, fence_token "
                "FROM leases WHERE table_name=?",
                (table,),
            ).fetchone()

            if row is None:
                # 无租约：若该 owner 还在等待队列里，顺手让它离队，
                # 避免放弃排队后变成永久阻挡后来者的"幽灵队头"。
                removed = conn.execute(
                    "DELETE FROM waiters WHERE table_name=? AND owner=?",
                    (table, owner),
                ).rowcount
                _audit(conn, table,
                       "wait_cancelled" if removed else "release_duplicate",
                       owner)
                return _release_response(table, owner, holder=None,
                                         released=False, duplicate=True)

            current_owner, expires_at, entry_count, token = row

            if now >= expires_at and current_owner != owner:
                # 租约已死，清场并审计，等待下一个 acquire 接管。
                conn.execute("DELETE FROM leases WHERE table_name=?", (table,))
                conn.execute(
                    "DELETE FROM waiters WHERE table_name=? AND owner=?",
                    (table, owner),
                )
                _audit(conn, table, "expired_cleanup", current_owner,
                       expired_at=expires_at)
                _audit(conn, table, "release_duplicate", owner)
                return _release_response(table, owner, holder=None,
                                         released=False, duplicate=True)

            if current_owner != owner:
                # 非持有者不能释放租约，但若它自己在排队，允许取消排队。
                conn.execute(
                    "DELETE FROM waiters WHERE table_name=? AND owner=?",
                    (table, owner),
                )
                _audit(conn, table, "release_rejected", owner,
                       holder=current_owner)
                return _release_response(table, owner, holder=current_owner,
                                         released=False, duplicate=False)

            new_count = entry_count - 1
            if new_count > 0:
                # 还有未关闭的重入，租约继续保持。
                conn.execute(
                    "UPDATE leases SET entry_count=? WHERE table_name=?",
                    (new_count, table),
                )
                _audit(conn, table, "release_reentrant", owner,
                       entry_count=new_count)
                return _release_response(table, owner, holder=owner,
                                         released=True, duplicate=False,
                                         entry_count=new_count,
                                         fence_token=token,
                                         expires_in=max(0.0, expires_at - now))

            conn.execute("DELETE FROM leases WHERE table_name=?", (table,))
            next_waiter = conn.execute(
                "SELECT owner FROM waiters WHERE table_name=? "
                "ORDER BY id LIMIT 1",
                (table,),
            ).fetchone()
            _audit(conn, table, "released", owner,
                   next_waiter=next_waiter[0] if next_waiter else None)
            return _release_response(table, owner, holder=None,
                                     released=True, duplicate=False,
                                     entry_count=0, fence_token=token,
                                     next_waiter=(next_waiter[0]
                                                  if next_waiter else None))
    finally:
        conn.close()


def _release_response(table, owner, holder, released, duplicate,
                      entry_count=0, fence_token=None, expires_in=0.0,
                      next_waiter=None):
    return {
        "table": table,
        "owner": owner,
        "holder": holder,
        "released": released,
        "duplicate": duplicate,
        "entry_count": entry_count,
        "fence_token": fence_token,
        "expires_in": expires_in,
        "next_waiter": next_waiter,
    }


def renew(table, owner, ttl=None):
    """续约，必须核对持有者；持有者不匹配或租约已过期则抛 LeaseError。"""
    if not table or not owner:
        raise ValueError("table 和 owner 必须是非空字符串")

    _init_db()
    conn = _connect()
    try:
        with _transaction(conn):
            now = _CLOCK()
            row = conn.execute(
                "SELECT owner, expires_at, entry_count, fence_token "
                "FROM leases WHERE table_name=?",
                (table,),
            ).fetchone()
            if row is None:
                raise LeaseError(f"表 {table} 不存在租约，无法续约")
            current_owner, expires_at, entry_count, token = row
            if current_owner != owner:
                raise LeaseError(
                    f"持有者不匹配：表 {table} 由 {current_owner} 持有，"
                    f"{owner} 无权续约")
            if now >= expires_at:
                raise LeaseError(f"租约已过期（{expires_at}），请重新获取")
            new_ttl = ttl if ttl is not None else (expires_at - now)
            if new_ttl <= 0:
                raise ValueError("ttl 必须为正数")
            new_expires = now + new_ttl
            conn.execute(
                "UPDATE leases SET expires_at=? WHERE table_name=?",
                (new_expires, table),
            )
            _audit(conn, table, "renewed", owner, ttl=new_ttl)
            return {
                "table": table,
                "owner": owner,
                "holder": owner,
                "renewed": True,
                "expires_in": new_ttl,
                "entry_count": entry_count,
                "fence_token": token,
            }
    finally:
        conn.close()


def status(table):
    """查看租约状态，只读，可跨进程调用。"""
    _init_db()
    conn = _connect()
    try:
        now = _CLOCK()
        row = conn.execute(
            "SELECT owner, expires_at, entry_count, fence_token "
            "FROM leases WHERE table_name=?",
            (table,),
        ).fetchone()
        waiters = conn.execute(
            "SELECT owner FROM waiters WHERE table_name=? ORDER BY id",
            (table,),
        ).fetchall()

        if row is None:
            return {
                "table": table,
                "holder": None,
                "granted": False,
                "expired": None,
                "expires_in": None,
                "entry_count": 0,
                "fence_token": None,
                "waiters": [w[0] for w in waiters],
                "wait_position": len(waiters),
            }

        owner, expires_at, entry_count, token = row
        expired = now >= expires_at
        return {
            "table": table,
            "holder": owner,
            "granted": True,
            "expired": expired,
            "expires_in": max(0.0, expires_at - now),
            "entry_count": entry_count,
            "fence_token": token,
            "waiters": [w[0] for w in waiters],
            "wait_position": len(waiters),
        }
    finally:
        conn.close()


def audit_log(table=None, limit=100):
    """读取审计记录（接管、拒绝、释放等），便于跨进程排查。"""
    _init_db()
    conn = _connect()
    try:
        if table is None:
            rows = conn.execute(
                "SELECT id, ts, table_name, event, owner, detail "
                "FROM lease_audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, ts, table_name, event, owner, detail "
                "FROM lease_audit WHERE table_name=? "
                "ORDER BY id DESC LIMIT ?",
                (table, limit),
            ).fetchall()
        return [
            {
                "id": r[0],
                "ts": r[1],
                "table": r[2],
                "event": r[3],
                "owner": r[4],
                "detail": json.loads(r[5]) if r[5] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()
