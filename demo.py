"""演示：读写双向兼容判定 + 历史版本回放 + 新规则 gate 上线。"""

from contractdiff import (
    check, RuleBook, Rule, History, Severity,
    evaluate_gate, enable_if_passed,
)
from contractdiff.schema import MISSING


def print_result(title, result):
    print(f"\n=== {title} ===")
    print(f"整体兼容: {result['compatible']}  "
          f"(读兼容: {result['read_compatible']}, 写兼容: {result['write_compatible']})")
    for f in result["findings"]:
        print(f"  [{f['severity']:>10}] {f['direction']:>5} {f['path']} "
              f"({f['rule']}): {f['message']}")
        print(f"               建议: {f['suggestion']}")


# 第一段：基础字段增删与类型变化（旧字符串简写形式）
print_result("删列：读写双向破坏",
             check({"fields": {"id": "int", "name": "text"}},
                   {"fields": {"id": "text"}}))

# 第二段：完整结构——类型收紧/放宽、默认值、枚举、必填、嵌套路径
old = {"fields": {
    "user_id": "int",
    "score": "int",
    "status": {"type": "text", "enum": ["on", "off"], "required": True},
    "profile": {"type": "record", "fields": {
        "age": {"type": "int", "default": 0},
        "city": "text",
    }},
}}
new = {"fields": {
    "user_id": "long",                                    # 写入方向破坏
    "score": "short",                                     # 读取方向破坏
    "status": {"type": "text", "enum": ["on", "off", "pending"],
               "required": True},                         # 枚举加值：写警告
    "profile": {"type": "record", "fields": {
        "age": {"type": "int", "default": 18},            # 默认值单独标记
        # city 被删除：双向破坏，路径 profile.city
    }},
    "tenant": {"type": "int", "required": True},          # 新增必填无默认：写破坏
}}
print_result("完整结构对比（每条结论带方向、字段路径、建议）", check(old, new))

# 第三段：历史版本回看 + 新规则影子扫描 + gate 验证后启用
history = History()
history.add("v1", {"fields": {
    "a": {"type": "int"},
    "b": {"type": "int", "default": 0}}})
history.add("v2", {"fields": {
    "a": {"type": "int", "default": 0},
    "b": {"type": "int", "default": 0}}})
history.add("v3", {"fields": {
    "a": {"type": "int", "default": 0},
    "b": {"type": "int", "default": 9}}})

print("\n=== 历史版本批量扫描 ===")
scan = history.scan()
for item in scan["results"]:
    r = item["result"]
    print(f"  {item['from_version']} -> {item['to_version']}: "
          f"兼容={r['compatible']}, 结论数={len(r['findings'])}")


def default_review_rule(rule, path, old_field, new_field, ctx):
    """候选规则：默认值变化升级为 warning，先以影子模式跑历史契约。"""
    from contractdiff.schema import render_value
    if old_field is None or new_field is None:
        return None
    if old_field["default"] is MISSING and new_field["default"] is MISSING:
        return None
    if old_field["default"] == new_field["default"]:
        return None
    return {
        "rule": rule.id, "direction": "both", "severity": Severity.WARNING,
        "path": path, "message": "影子规则：默认值变化需人工复核",
        "suggestion": "人工确认默认值变更的回填与下游影响",
        "old": render_value(old_field["default"]),
        "new": render_value(new_field["default"]),
    }


book = RuleBook()
book.register(Rule("default.review", "默认值人工复核", False, default_review_rule))

# 人工判断：v1->v2 命中 a（无默认 -> 0）；v2->v3 命中 b（0 -> 9）
gate = evaluate_gate(book, "default.review", history, [
    {"from_version": "v1", "to_version": "v2", "expected": ["a"]},
    {"from_version": "v2", "to_version": "v3", "expected": ["b"]},
])
print("\n=== 新规则 gate ===")
for case in gate["cases"]:
    print(f"  {case['from_version']} -> {case['to_version']}: "
          f"人工={case['expected']} 规则={case['actual']} 一致={case['match']}")
print(f"gate 通过: {gate['passed']}，启用结果: "
      f"{enable_if_passed(book, 'default.review', gate)}")
