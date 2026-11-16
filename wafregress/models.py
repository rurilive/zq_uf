"""领域模型：用例、规则，以及匹配/判定逻辑。

判定结果有四种（拦截侧、放行侧各两类）：
- blocked         攻击用例，被规则拦截（正确）
- false_positive  业务用例，却被规则拦截（误拦）
- missed          攻击用例，没有任何规则拦截（漏拦，保留触发请求用于复现）
- passed          业务用例，正常放行（正确）
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from .errors import WafValidationError

EXPECT_BLOCK = "block"
EXPECT_PASS = "pass"

OUTCOME_BLOCKED = "blocked"
OUTCOME_FALSE_POSITIVE = "false_positive"
OUTCOME_MISSED = "missed"
OUTCOME_PASSED = "passed"

VALID_EXPECT = (EXPECT_BLOCK, EXPECT_PASS)


@dataclass(frozen=True)
class Rule:
    """一条 WAF 规则。

    pattern: 子串匹配，出现在请求里即视为命中（与旧版行为一致）。
    scope:   可选，限定规则只对哪个业务线的用例生效；
             不填表示全局规则，对所有业务线生效。
    """

    id: str
    pattern: str
    scope: Optional[str] = None
    description: str = ""

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise WafValidationError("rule must be a dict")
        rule_id = data.get("id")
        pattern = data.get("pattern")
        if not rule_id or not isinstance(rule_id, str):
            raise WafValidationError("rule.id must be a non-empty string")
        if pattern is None or not isinstance(pattern, str):
            raise WafValidationError(f"rule {rule_id!r}: pattern must be a string")
        scope = data.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise WafValidationError(f"rule {rule_id!r}: scope must be a string")
        return cls(
            id=rule_id,
            pattern=pattern,
            scope=scope,
            description=data.get("description", ""),
        )

    def applies_to(self, biz: Optional[str]) -> bool:
        """规则是否作用于指定业务线的用例。"""
        return self.scope is None or self.scope == biz

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Case:
    """一条回归用例。

    biz:          业务线（search/login/pay...），用例集按业务线分开维护。
    expect:       block=攻击用例期望拦截；pass=业务用例期望放行。
    expect_rule:  可选，攻击用例期望由哪条规则拦；用于漏拦的按规则归因。
    scenario:     业务场景（如 note/nickname），误拦用例按它分组。
    """

    id: str
    biz: str
    request: str
    expect: str
    scenario: str = "default"
    expect_rule: Optional[str] = None
    description: str = ""

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise WafValidationError("case must be a dict")
        case_id = data.get("id")
        biz = data.get("biz")
        request = data.get("request")
        expect = data.get("expect")
        for name, value in (("id", case_id), ("biz", biz), ("request", request)):
            if not value or not isinstance(value, str):
                raise WafValidationError(f"case.{name} must be a non-empty string")
        if expect not in VALID_EXPECT:
            raise WafValidationError(
                f"case {case_id!r}: expect must be one of {VALID_EXPECT}"
            )
        expect_rule = data.get("expect_rule")
        if expect_rule is not None and not isinstance(expect_rule, str):
            raise WafValidationError(f"case {case_id!r}: expect_rule must be a string")
        scenario = data.get("scenario", "default")
        if not isinstance(scenario, str) or not scenario:
            raise WafValidationError(f"case {case_id!r}: scenario must be a string")
        return cls(
            id=case_id,
            biz=biz,
            request=request,
            expect=expect,
            scenario=scenario,
            expect_rule=expect_rule,
            description=data.get("description", ""),
        )

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class CaseResult:
    """单条用例的回归结果，也是留存/复现的最小单元。"""

    case_id: str
    biz: str
    scenario: str
    expect: str
    outcome: str
    request: str
    matched_rules: List[str] = field(default_factory=list)
    blocking_rule: Optional[str] = None
    expect_rule: Optional[str] = None

    @property
    def is_correct(self) -> bool:
        return self.outcome in (OUTCOME_BLOCKED, OUTCOME_PASSED)

    def to_dict(self):
        return asdict(self)


def match_rules(case: Case, rules: List[Rule]) -> List[str]:
    """返回所有命中该用例请求的规则 id（按规则传入顺序）。

    先按业务线过滤规则作用域，再做子串匹配，保证不同业务线互不干扰。
    """
    hits = []
    for rule in rules:
        if rule.applies_to(case.biz) and rule.pattern in case.request:
            hits.append(rule.id)
    return hits


def evaluate(case: Case, rules: List[Rule]) -> CaseResult:
    """对单条用例跑一遍规则并归类到四种结果之一。"""
    matched = match_rules(case, rules)
    blocking_rule = matched[0] if matched else None
    if matched:
        # 被拦截：攻击用例是正确拦截，业务用例是误拦。
        outcome = (
            OUTCOME_BLOCKED if case.expect == EXPECT_BLOCK else OUTCOME_FALSE_POSITIVE
        )
    else:
        # 放行：攻击用例是漏拦，业务用例是正常放行。
        outcome = (
            OUTCOME_MISSED if case.expect == EXPECT_BLOCK else OUTCOME_PASSED
        )
    return CaseResult(
        case_id=case.id,
        biz=case.biz,
        scenario=case.scenario,
        expect=case.expect,
        outcome=outcome,
        request=case.request,
        matched_rules=matched,
        blocking_rule=blocking_rule,
        expect_rule=case.expect_rule,
    )
