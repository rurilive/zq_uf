"""误报标记与用例回灌。

安全同学把确认的误报标记成用例：之后同样的会话再命中，对应规则结论会被
抑制并在结果里标注；用例也可以导出成 JSON 沉淀到仓库，或直接跑回归，
验证规则改动后该会话确实不再告警（或按预期重新告警）。
"""

import json
import uuid


class FeedbackStore:
    def __init__(self):
        # session_id -> {case_id, pcap, session, rule_id, reason, marked_at...}
        self._cases = {}

    def mark_false_positive(self, session, rule_id, reason="", pcap=""):
        """把某会话的某条规则命中标记为误报，返回用例 ID。"""
        if rule_id not in session.verdict:
            raise ValueError(f"会话 {session.sid} 未命中规则 {rule_id}，无法标记")
        case_id = uuid.uuid4().hex[:12]
        case = {
            "case_id": case_id,
            "pcap": pcap,
            "session_id": session.sid,
            "five_tuple": list(session.key),
            "rule_id": rule_id,
            "type": session.verdict[rule_id]["type"],
            "reason": reason,
            "packets": list(session.packets),
        }
        self._cases[session.sid] = case
        if rule_id not in session.suppressed:
            session.suppressed.append(rule_id)
        return case

    def apply(self, sessions):
        """重跑分析后按会话 ID 重新挂抑制标记。"""
        index = {s.sid: s for s in sessions}
        for case in self._cases.values():
            sess = index.get(case["session_id"])
            if sess is not None and case["rule_id"] not in sess.suppressed:
                sess.suppressed.append(case["rule_id"])

    def cases(self):
        return [dict(case) for case in self._cases.values()]

    def export_cases(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.cases(), fh, ensure_ascii=False, indent=2)
        return path

    def import_cases(self, path):
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
        for row in rows:
            self._cases[row["session_id"]] = dict(row)
        return len(rows)


def run_cases(store, analyze_fn, pcaps):
    """跑误报回归用例。

    analyze_fn(pcap) 对每个涉及的抓包重跑一次分析；用例对应会话的目标规则
    仍然产生未抑制的命中即视为回归。返回逐条用例结果。
    """
    results = []
    index = {}
    for pcap in pcaps:
        out = analyze_fn(pcap)
        for sess in out["_sessions"]:
            index[sess.sid] = (pcap, sess)

    for case in store.cases():
        located = index.get(case["session_id"])
        if located is None:
            results.append({**case, "status": "missing", "detail": "抓包中未找到该会话"})
            continue
        pcap, sess = located
        hit = sess.verdict.get(case["rule_id"])
        if hit is None:
            status, detail = "pass", "规则不再命中该会话"
        elif case["rule_id"] in sess.suppressed:
            status, detail = "pass", "命中但已被误报用例抑制"
        else:
            status, detail = "regressed", f"规则 {case['rule_id']} 重新告警"
        results.append({**case, "status": status, "detail": detail})
    return results
