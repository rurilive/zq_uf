"""Fencing token 守护的安全写表。

租约被接管意味着原持有者可能已经崩溃，也可能只是卡顿后"复活"。
仅凭租约无法阻止一个过期持有者继续写业务表，因此每次新发放
（含接管）都生成更大的 fencing token，业务表只接受不小于已见 token
的写入——过期持有者的小 token 写入直接被拒，接管者的数据不会被覆盖。
"""

import sqlite3

from .lease import LeaseError, _connect, _init_db


class FenceRejected(LeaseError):
    """过期持有者（fencing token 落后）的写入被拒绝。"""


class FencedWriter:
    """在同一个 SQLite 库里对业务数据表做带 fencing 校验的写入。

    业务表约定：``(id TEXT PRIMARY KEY, payload TEXT, fence_token INTEGER)``。
    生产环境可把同样的 token 比较逻辑放到目标数据库的写入层。
    """

    def __init__(self, db_path):
        self.db_path = db_path
        _init_db()
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS data_writes ("
                "id TEXT PRIMARY KEY, "
                "payload TEXT NOT NULL, "
                "fence_token INTEGER NOT NULL)"
            )
            conn.commit()
        finally:
            conn.close()

    def write(self, row_id, payload, fence_token):
        """带 fencing token 写一行。

        - 新行：直接写入并记录 token；
        - 已存在：仅当本次 token 不小于已记录 token 时才允许覆盖；
        - token 落后（过期持有者的迟到写入）：抛 :class:`FenceRejected`，
          原数据原样保留。
        """
        if not isinstance(fence_token, int) or fence_token <= 0:
            raise ValueError("fence_token 必须是正整数")
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT fence_token, payload FROM data_writes WHERE id=?",
                (row_id,),
            ).fetchone()
            if row is not None and fence_token < row[0]:
                conn.execute("ROLLBACK")
                raise FenceRejected(
                    f"写入被 fencing 拒绝：行 {row_id} 已见 token {row[0]}，"
                    f"迟到写入 token {fence_token}（持有者可能已被接管）")
            conn.execute(
                "INSERT INTO data_writes(id, payload, fence_token) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
                "fence_token=excluded.fence_token",
                (row_id, payload, fence_token),
            )
            conn.execute("COMMIT")
            return {"id": row_id, "written": True, "fence_token": fence_token}
        except Exception:
            # FenceRejected 分支已显式 ROLLBACK；其余异常此处兜底，
            # 无活动事务时忽略二次回滚错误。
            try:
                conn.rollback()
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            conn.close()

    def read(self, row_id):
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            row = conn.execute(
                "SELECT id, payload, fence_token FROM data_writes WHERE id=?",
                (row_id,),
            ).fetchone()
            if row is None:
                return None
            return {"id": row[0], "payload": row[1], "fence_token": row[2]}
        finally:
            conn.close()

    def all_rows(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            rows = conn.execute(
                "SELECT id, payload, fence_token FROM data_writes ORDER BY id"
            ).fetchall()
            return [
                {"id": r[0], "payload": r[1], "fence_token": r[2]}
                for r in rows
            ]
        finally:
            conn.close()
