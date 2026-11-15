"""新规则上线闸门：影子模式扫描历史契约，与人工判断一致后才允许启用。"""

from .engine import RuleBook
from .history import History


def evaluate_gate(rulebook, rule_id, history, expectations):
    """用 disabled 规则的影子结果回放历史版本对，逐规则核对人工判断。

    expectations: [{"from_version", "to_version",
                    "expected": ["field.path", ...] | "none"}]
    expected 为该规则在这对版本上应命中的字段路径列表（排序后比较）；
    "none" 表示不应命中。

    返回：
      {
        "rule_id", "passed",
        "cases": [{"from_version", "to_version", "expected", "actual",
                   "match"}],
        "mismatches": [...]  # 未通过的 case
      }
    """
    if not isinstance(rulebook, RuleBook):
        raise TypeError("rulebook 必须是 RuleBook 实例")
    if rule_id not in rulebook.rule_ids():
        raise KeyError(f"未知规则: {rule_id}")
    if rulebook.is_enabled(rule_id):
        raise ValueError(
            f"规则 {rule_id} 已启用；gate 只针对影子（disabled）规则")

    cases = []
    for exp in expectations:
        old, new = exp["from_version"], exp["to_version"]
        result = rulebook.check(
            history.get(old), history.get(new), include_disabled=True)
        actual = sorted(
            f["path"] for f in result["findings"] if f["rule"] == rule_id)
        expected = [] if exp["expected"] == "none" else sorted(exp["expected"])
        match = actual == expected
        cases.append({
            "from_version": old,
            "to_version": new,
            "expected": expected,
            "actual": actual,
            "match": match,
        })

    mismatches = [c for c in cases if not c["match"]]
    return {
        "rule_id": rule_id,
        "passed": not mismatches,
        "cases": cases,
        "mismatches": mismatches,
    }


def enable_if_passed(rulebook, rule_id, gate_result):
    """gate 通过则启用规则并返回 True；否则保持 disabled 返回 False。"""
    if gate_result["rule_id"] != rule_id or not gate_result["passed"]:
        return False
    rulebook.enable(rule_id)
    return True
