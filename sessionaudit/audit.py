"""带审计与回收的采集会话管理（纯标准库）。

口径：
- ``use(account)`` 复用当前站点下未过期的会话；会话过期则自动失效并新建，
  每次使用都写一条审计（账号、站点、时间）。
- ``sweep(now)`` 用统一时钟判定过期，回收前确认没有在跑任务、先释放句柄，
  删除文件失败会重试，并按保留期裁剪审计日志。
- 审计记录可按账号、站点、事件查询；会话状态可通过 ``export_state`` 导出。
"""

import atexit
import contextlib
import json
import os
import tempfile
import time

ACTIVE = "active"
EXPIRED = "expired"
RECLAIMED = "reclaimed"


class Session:
    """一个采集会话，对应磁盘上的会话文件和一个保持打开的句柄。"""

    def __init__(self, account, site, session_id, path, handle, created_at, ttl):
        self.account = account
        self.site = site
        self.session_id = session_id
        self.path = path
        self.handle = handle
        self.created_at = created_at
        self.last_used_at = created_at
        self.ttl = ttl
        self.status = ACTIVE
        self.busy = 0
        self.uses = 1

    def is_expired(self, now):
        # 滑动 TTL：最后一次使用时间 + ttl 到期即失效
        return self.last_used_at + self.ttl <= now

    @property
    def expires_at(self):
        return self.last_used_at + self.ttl

    def to_dict(self):
        return {
            "account": self.account,
            "site": self.site,
            "session": self.session_id,
            "path": self.path,
            "status": self.status,
            "busy": self.busy,
            "uses": self.uses,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "expires_at": self.expires_at,
            "handle_open": not self.handle.closed,
        }


class AuditLog:
    """JSON Lines 审计日志：内存索引 + 磁盘追加，支持查询与保留期裁剪。"""

    def __init__(self, path, site):
        self.path = path
        self.site = site
        self._records = []
        self._seq = 0
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    self._records.append(record)
                    self._seq = max(self._seq, record.get("id", 0))

    def append(self, event, account, session, now, detail=None):
        self._seq += 1
        record = {
            "id": self._seq,
            "time": now,
            "site": self.site,
            "account": account,
            "session": session,
            "event": event,
            "detail": detail or {},
        }
        self._records.append(record)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def query(self, account=None, site=None, event=None):
        result = self._records
        if account is not None:
            result = [r for r in result if r["account"] == account]
        if site is not None:
            result = [r for r in result if r["site"] == site]
        if event is not None:
            result = [r for r in result if r["event"] == event]
        return list(result)

    def prune(self, before):
        """裁剪 time < before 的记录并重写日志，返回被裁剪的记录。"""
        kept = [r for r in self._records if r["time"] >= before]
        pruned = [r for r in self._records if r["time"] < before]
        if not pruned:
            return []
        self._records = kept
        with open(self.path, "w", encoding="utf-8") as f:
            for record in self._records:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return pruned


class SessionManager:
    """会话管理器：时钟、TTL、审计保留期、删除重试均可注入配置。"""

    def __init__(
        self,
        store_dir=None,
        site="default",
        ttl=3600,
        audit_retention=7 * 24 * 3600,
        clock=time.time,
        retry_attempts=3,
        sleep=time.sleep,
        remove=os.remove,
    ):
        self.site = site
        self.ttl = ttl
        self.audit_retention = audit_retention
        self.retry_attempts = max(1, retry_attempts)
        self._clock = clock
        self._sleep = sleep
        self._remove = remove
        self._dir = store_dir or os.path.join(tempfile.gettempdir(), "sessionaudit")
        os.makedirs(self._dir, exist_ok=True)
        self._all = []
        self._current = {}
        self._counters = {}
        self._audit = AuditLog(os.path.join(self._dir, "audit.log"), site)

    def _resolve_now(self, now):
        # 统一时钟：显式传 now 时以传入值为准，否则取注入时钟
        return self._clock() if now is None else now

    def use(self, account):
        """返回账号当前可用的会话；过期会话自动失效并新建，每次使用写审计。"""
        now = self._clock()
        session = self._current.get(account)
        if session is None or session.status != ACTIVE:
            session = self._create(account, now)
        elif session.is_expired(now):
            session.status = EXPIRED
            self._audit.append(
                "expire", account, session.session_id, now,
                {"reason": "ttl", "expires_at": session.expires_at},
            )
            session = self._create(account, now)
        else:
            session.uses += 1
            session.last_used_at = now
            self._write_session_file(session)
        self._audit.append(
            "use", account, session.session_id, now, {"uses": session.uses}
        )
        # 审计明细走日志查询（query_audit），响应内不携带历史记录
        return {
            "account": account,
            "site": self.site,
            "session": session.session_id,
            "audit": [],
        }

    @contextlib.contextmanager
    def task(self, account):
        """标记任务在跑；回收前会检查，在跑的过期会话只失效不删除。"""
        self.use(account)
        session = self._current[account]
        session.busy += 1
        try:
            yield session
        finally:
            session.busy -= 1

    def sweep(self, now=None):
        """过期失效 -> 安全回收 -> 审计裁剪，返回完整处置结果。"""
        now = self._resolve_now(now)
        expired = []
        reclaimed = []
        skipped = []
        failed = []
        sweep_audit = []

        # 1. 统一时钟判定过期：先失效（不可再被 use 复用），再回收文件
        for session in self._all:
            if session.status == ACTIVE and session.is_expired(now):
                session.status = EXPIRED
                expired.append(session.session_id)
                sweep_audit.append(
                    self._audit.append(
                        "expire", session.account, session.session_id, now,
                        {"reason": "ttl", "expires_at": session.expires_at},
                    )
                )

        # 2. 安全回收：确认无在跑任务 -> 先释放句柄 -> 删除文件（失败重试）
        for session in self._all:
            if session.status != EXPIRED:
                continue
            if session.busy > 0:
                skipped.append(session.session_id)
                sweep_audit.append(
                    self._audit.append(
                        "skip_reclaim", session.account, session.session_id, now,
                        {"reason": "busy", "busy": session.busy},
                    )
                )
                continue
            if not session.handle.closed:
                session.handle.close()
            if self._remove_with_retry(session.path):
                session.status = RECLAIMED
                reclaimed.append(session.session_id)
                sweep_audit.append(
                    self._audit.append(
                        "reclaim", session.account, session.session_id, now,
                        {"path": session.path},
                    )
                )
            else:
                failed.append(session.session_id)
                sweep_audit.append(
                    self._audit.append(
                        "reclaim_failed", session.account, session.session_id, now,
                        {"attempts": self.retry_attempts},
                    )
                )

        # 3. 审计日志按保留期裁剪（None 表示永久保留）
        audit_pruned = []
        if self.audit_retention is not None:
            audit_pruned = self._audit.prune(now - self.audit_retention)
            if audit_pruned:
                sweep_audit.append(
                    self._audit.append(
                        "audit_pruned", None, None, now,
                        {"count": len(audit_pruned), "retention": self.audit_retention},
                    )
                )

        return {
            "now": now,
            "expired": expired,
            "reclaimed": reclaimed,
            "skipped": skipped,
            "failed": failed,
            "audit": sweep_audit,
            "audit_pruned": audit_pruned,
        }

    def query_audit(self, account=None, site=None, event=None):
        """按账号、站点、事件查询审计记录，参数为 None 表示不过滤。"""
        return self._audit.query(account=account, site=site, event=event)

    def export_state(self, path=None):
        """导出全部会话状态；给 path 时同时写一份 JSON 文件。"""
        state = {
            "site": self.site,
            "ttl": self.ttl,
            "audit_retention": self.audit_retention,
            "generated_at": self._clock(),
            "sessions": [session.to_dict() for session in self._all],
            "audit_records": len(self._audit.query()),
        }
        if path is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, sort_keys=True, indent=2)
        return state

    def close(self):
        for session in self._all:
            if not session.handle.closed:
                try:
                    session.handle.close()
                except OSError:
                    pass

    def _create(self, account, now):
        self._counters[account] = self._counters.get(account, 0) + 1
        session_id = f"s-{account}-{self._counters[account]}"
        path = os.path.join(self._dir, f"{session_id}.json")
        handle = open(path, "w+", encoding="utf-8")
        session = Session(account, self.site, session_id, path, handle, now, self.ttl)
        self._write_session_file(session)
        self._all.append(session)
        self._current[account] = session
        return session

    @staticmethod
    def _write_session_file(session):
        session.handle.seek(0)
        session.handle.truncate()
        session.handle.write(
            json.dumps(session.to_dict(), ensure_ascii=False, sort_keys=True)
        )
        session.handle.flush()

    def _remove_with_retry(self, path):
        for attempt in range(1, self.retry_attempts + 1):
            try:
                self._remove(path)
                return True
            except FileNotFoundError:
                return True
            except OSError:
                if attempt >= self.retry_attempts:
                    return False
                self._sleep(attempt * 0.01)
        return False


_default_manager = None


def get_manager():
    global _default_manager
    if _default_manager is None:
        _default_manager = SessionManager()
        atexit.register(_default_manager.close)
    return _default_manager


def reset_default_manager():
    global _default_manager
    if _default_manager is not None:
        _default_manager.close()
    _default_manager = None


def use(account):
    """模块级便捷接口：使用账号会话并写审计（接口签名保持不变）。"""
    return get_manager().use(account)


def sweep(now=None):
    """模块级便捷接口：过期失效、安全回收与审计裁剪（接口签名保持不变）。"""
    return get_manager().sweep(now)


def query_audit(account=None, site=None, event=None):
    """模块级便捷接口：按账号、站点、事件查询审计。"""
    return get_manager().query_audit(account=account, site=site, event=event)


def export_state(path=None):
    """模块级便捷接口：导出会话状态。"""
    return get_manager().export_state(path)
