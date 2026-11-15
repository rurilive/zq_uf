"""规则引擎与对外 check 入口。"""

from .rules import RULES, Direction, Severity
from .schema import normalize_contract


class RuleBook:
    """规则集：默认启用内置规则；新规则先注册为 disabled（影子模式），
    通过历史回放 gate 后再 enable。"""

    def __init__(self, rules=None):
        self._rules = list(rules if rules is not None else RULES)
        self._enabled = {r.id: r.enabled_by_default for r in self._rules}

    # ------------------------------------------------------------ 规则管理
    def register(self, rule):
        """注册规则；默认 disabled，需经 gate 验证后显式启用。"""
        self._rules = [r for r in self._rules if r.id != rule.id]
        self._rules.append(rule)
        self._enabled[rule.id] = rule.enabled_by_default
        return self

    def enable(self, rule_id):
        if rule_id not in self._enabled:
            raise KeyError(f"未知规则: {rule_id}")
        self._enabled[rule_id] = True
        return self

    def disable(self, rule_id):
        if rule_id not in self._enabled:
            raise KeyError(f"未知规则: {rule_id}")
        self._enabled[rule_id] = False
        return self

    def is_enabled(self, rule_id):
        return self._enabled.get(rule_id, False)

    def rule_ids(self):
        return [r.id for r in self._rules]

    def _get(self, rule_id):
        for r in self._rules:
            if r.id == rule_id:
                return r
        raise KeyError(f"未知规则: {rule_id}")

    # ------------------------------------------------------------ 执行
    def check(self, old, new, include_disabled=False):
        """比对两份契约。include_disabled=True 时影子规则也产出 finding，
        供 gate 回放，不影响正式判定。"""
        old_norm = normalize_contract(old)
        new_norm = normalize_contract(new)
        findings = []
        ctx = {"rulebook": self, "include_disabled": include_disabled}

        def walk(prefix, old_fields, new_fields):
            all_names = sorted(set(old_fields) | set(new_fields))
            for name in all_names:
                path = f"{prefix}.{name}" if prefix else name
                old_field = old_fields.get(name)
                new_field = new_fields.get(name)

                for rule in self._rules:
                    if not include_disabled and not self._enabled[rule.id]:
                        continue
                    result = rule.func(rule, path, old_field, new_field, ctx)
                    if result is None:
                        continue
                    findings.extend(result if isinstance(result, list) else [result])

                # 嵌套 record：两侧都存在且都是 record 时递归；
                # 类型不一致已由 type.changed 规则报双向破坏，不递归。
                if (old_field and new_field
                        and old_field["type"] == new_field["type"] == "record"):
                    walk(path, old_field["fields"], new_field["fields"])

        walk("", old_norm["fields"], new_norm["fields"])
        return build_result(findings)


def build_result(findings):
    """汇总成对外结果：兼容/破坏/建议清单，并按读写方向分桶。"""
    findings = sorted(findings, key=lambda f: (f["path"], f["rule"], f["direction"]))
    read_breaking = [f for f in findings
                     if f["severity"] == Severity.BREAKING
                     and f["direction"] in (Direction.READ, Direction.BOTH)]
    write_breaking = [f for f in findings
                      if f["severity"] == Severity.BREAKING
                      and f["direction"] in (Direction.WRITE, Direction.BOTH)]

    return {
        "compatible": not read_breaking and not write_breaking,
        "read_compatible": not read_breaking,
        "write_compatible": not write_breaking,
        "breaking": [f for f in findings if f["severity"] == Severity.BREAKING],
        "warnings": [f for f in findings if f["severity"] == Severity.WARNING],
        "info": [f for f in findings if f["severity"] == Severity.INFO],
        "compatible_items": [f for f in findings
                             if f["severity"] == Severity.COMPATIBLE],
        "findings": findings,
        "suggestions": [
            {"path": f["path"], "direction": f["direction"],
             "rule": f["rule"], "suggestion": f["suggestion"]}
            for f in findings
            if f["severity"] in (Severity.BREAKING, Severity.WARNING)
        ],
    }


# 默认规则册：全部内置规则启用，新注册规则默认影子
DEFAULT_RULEBOOK = RuleBook()


def check(old, new, rulebook=None):
    """契约兼容性检查（旧契约 -> 新契约）。"""
    return (rulebook or DEFAULT_RULEBOOK).check(old, new)
