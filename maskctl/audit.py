"""审计日志：每条记录包含操作人和时间。"""
from datetime import datetime, timezone


class AuditLog:
    def __init__(self):
        self.entries = []

    def record(self, actor, action, table=None, ok=True, detail=None):
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "table": table,
            "ok": ok,
            "detail": detail or {},
        }
        self.entries.append(entry)
        return entry
