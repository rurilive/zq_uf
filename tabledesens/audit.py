"""审计日志：内存留存 + 按操作人/时间归档到文件。

归档目录结构（每个事件一个 JSON 行文件，按 UTC 日期分目录、操作人
分子目录、毫秒时间戳命名，天然按操作人和时间两个维度检索）::

    <audit_dir>/<YYYY-MM-DD>/<operator>/<HHMMSS-millis>-<action>-<seq>.json

不传 audit_dir 时仅保留在内存，测试和临时联调不落地任何文件。
"""

import datetime
import json
import os


class AuditLog:
    """脱敏/还原事件的审计记录器。"""

    def __init__(self, audit_dir=None):
        self.audit_dir = audit_dir
        # 内存中的全量事件，顺序即发生顺序
        self.events = []
        self._seq = 0

    def record(self, action, operator, node, **details):
        """登记一条审计事件并返回事件 dict。

        action 例如 "mask" / "reveal" / "reveal_denied" / "revoke"；
        operator 为 None 时统一记为 "anonymous"。
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        event = {
            "ts": now.isoformat(),
            "action": action,
            "operator": operator or "anonymous",
            "node": node,
        }
        event.update(details)
        self.events.append(event)
        self._seq += 1
        if self.audit_dir:
            self._archive(event, now)
        return event

    def _archive(self, event, now):
        day_dir = os.path.join(
            self.audit_dir, now.strftime("%Y-%m-%d"), event["operator"])
        os.makedirs(day_dir, exist_ok=True)
        filename = "%s-%s-%06d.json" % (
            now.strftime("%H%M%S-%f")[:-3], event["action"], self._seq)
        path = os.path.join(day_dir, filename)
        # 同一事件先写临时文件再改名，避免归档中途被读到半截 JSON
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(event, fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_path, path)

    def query(self, operator=None, date=None, action=None, node=None):
        """按操作人/UTC 日期(YYYY-MM-DD)/动作/节点过滤审计事件。"""
        result = []
        for event in self.events:
            if operator is not None and event["operator"] != operator:
                continue
            if date is not None and not event["ts"].startswith(date):
                continue
            if action is not None and event["action"] != action:
                continue
            if node is not None and event["node"] != node:
                continue
            result.append(event)
        return result
