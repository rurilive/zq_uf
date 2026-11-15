"""字段级处理规则、默认策略兜底与规则变更审批。

规则变更必须审批后生效；审批记录保留、可按表汇总、可导出，
审批人和值班同学可以查看。规则变更只影响之后写入的数据，
历史数据不重新处理。
"""
import csv
import io
import itertools
from dataclasses import dataclass
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Rule:
    """保留前后缀、中间遮蔽的处理规则。默认全遮蔽。"""

    keep_prefix: int = 0
    keep_suffix: int = 0
    mask_char: str = "*"

    def apply(self, value):
        text = "" if value is None else str(value)
        n = len(text)
        if n == 0:
            return ""
        if n <= self.keep_prefix + self.keep_suffix:
            return self.mask_char * n
        head = text[: self.keep_prefix] if self.keep_prefix else ""
        tail = text[n - self.keep_suffix :] if self.keep_suffix else ""
        return head + self.mask_char * (n - self.keep_prefix - self.keep_suffix) + tail


class RuleRegistry:
    """(表, 字段) 维度的规则注册表。

    未配规则且未声明为明文的字段一律按 default_rule 处理，
    新增采集字段自动继承默认策略，不会漏配。
    """

    def __init__(self, default_rule=None, approvers=(), oncall=()):
        self.default_rule = default_rule or Rule()
        self.approvers = set(approvers)
        self.oncall = set(oncall)
        self.version = 0  # 每次审批通过的变更 +1，写入清单便于追溯
        self._rules = {}  # (table, field) -> Rule
        self._plain = set()  # (table, field) 明确不处理
        self._changes = {}  # change_id -> 变更单
        self._approvals = []  # 审批记录
        self._seq = itertools.count(1)

    def rule_for(self, table, field):
        """返回 (rule, is_plain, source)，source ∈ rule/default/plain。"""
        key = (table, field)
        if key in self._plain:
            return None, True, "plain"
        rule = self._rules.get(key)
        if rule is not None:
            return rule, False, "rule"
        return self.default_rule, False, "default"

    # ---- 规则变更审批 ----

    def submit_change(self, proposer, table, field, rule=None, plain=False):
        """提交规则变更，返回变更单号；审批通过前不生效。"""
        if rule is None and not plain:
            raise ValueError("rule 和 plain 至少提供一个")
        cid = f"chg-{next(self._seq)}"
        self._changes[cid] = {
            "change_id": cid,
            "table": table,
            "field": field,
            "rule": rule,
            "plain": plain,
            "proposer": proposer,
            "status": "pending",
            "submitted_at": _now(),
        }
        return cid

    def _settle(self, change_id, approver, action):
        chg = self._changes.get(change_id)
        if chg is None:
            raise KeyError(f"unknown change: {change_id}")
        if approver not in self.approvers:
            raise PermissionError(f"{approver} 无审批权限")
        if chg["status"] != "pending":
            raise ValueError(f"变更单 {change_id} 已处理")
        chg["status"] = action
        self._approvals.append(
            {
                "change_id": change_id,
                "table": chg["table"],
                "field": chg["field"],
                "proposer": chg["proposer"],
                "approver": approver,
                "action": action,
                "time": _now(),
            }
        )
        return chg

    def approve(self, change_id, approver):
        """审批通过并生效。只影响之后写入的数据，历史数据不重新处理。"""
        chg = self._settle(change_id, approver, "approved")
        key = (chg["table"], chg["field"])
        if chg["plain"]:
            self._plain.add(key)
            self._rules.pop(key, None)
        else:
            self._rules[key] = chg["rule"]
            self._plain.discard(key)
        self.version += 1
        return chg

    def reject(self, change_id, approver):
        return self._settle(change_id, approver, "rejected")

    # ---- 审批记录查看与导出（审批人 / 值班同学） ----

    def _check_view(self, viewer):
        if viewer not in self.approvers and viewer not in self.oncall:
            raise PermissionError("仅审批人或值班同学可查看审批记录")

    def approvals(self, table=None, viewer=None):
        self._check_view(viewer)
        return [
            dict(r) for r in self._approvals if table is None or r["table"] == table
        ]

    def approval_summary(self, viewer):
        """按表汇总审批记录。"""
        self._check_view(viewer)
        summary = {}
        for r in self._approvals:
            s = summary.setdefault(r["table"], {"approved": 0, "rejected": 0})
            s[r["action"]] += 1
        return summary

    def export_approvals(self, table=None, viewer=None):
        """导出审批记录 CSV，可按表过滤。"""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            ["change_id", "table", "field", "proposer", "approver", "action", "time"]
        )
        for r in self.approvals(table=table, viewer=viewer):
            w.writerow(
                [
                    r["change_id"],
                    r["table"],
                    r["field"],
                    r["proposer"],
                    r["approver"],
                    r["action"],
                    r["time"],
                ]
            )
        return buf.getvalue()
