"""异常类型。

所有参数类错误都继承 WafValidationError（同时也是 ValueError），
调用方既可以按本包异常精确捕获，也可以沿用旧代码的 ValueError 捕获方式。
"""


class WafValidationError(ValueError):
    """入参或用例/规则数据不合法。"""


class DuplicateCaseId(WafValidationError):
    """用例 id 重复。"""

    def __init__(self, case_id):
        super().__init__(f"duplicate case id: {case_id}")
        self.case_id = case_id


class DuplicateRuleId(WafValidationError):
    """规则 id 重复。"""

    def __init__(self, rule_id):
        super().__init__(f"duplicate rule id: {rule_id}")
        self.rule_id = rule_id


class RunNotFound(WafValidationError):
    """指定的回归轮次不存在。"""

    def __init__(self, run_id):
        super().__init__(f"run not found: {run_id}")
        self.run_id = run_id


class CaseNotFound(WafValidationError):
    """按 id 找不到用例。"""

    def __init__(self, case_id):
        super().__init__(f"case not found: {case_id}")
        self.case_id = case_id


class NoBaselineRun(WafValidationError):
    """没有可用于对比/变更分析的上一轮结果。"""

    def __init__(self):
        super().__init__("no baseline run available; run regress() at least once")
