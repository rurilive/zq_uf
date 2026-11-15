"""归一化规则管道。

每条规则带稳定 id 与版本号，按顺序作用于 URL 字符串。
规则版本变化后由索引层做局部重算（见 index.py）。
"""


class Rule:
    """一条归一化规则：id + 版本 + 纯函数 str -> str。"""

    __slots__ = ("id", "_apply", "version", "description")

    def __init__(self, rule_id, apply, version=1, description=""):
        if not callable(apply):
            raise TypeError("rule apply must be callable")
        self.id = rule_id
        self._apply = apply
        self.version = version
        self.description = description

    def apply(self, value):
        return self._apply(value)

    def replaced(self, apply, version=None, description=None):
        """返回同 id 的新版本规则，版本号默认自增。"""
        return Rule(
            self.id,
            apply,
            version=self.version + 1 if version is None else version,
            description=self.description if description is None else description,
        )


class SameResult:
    """same() 的结果。

    布尔语义与旧接口一致（assertTrue/assertFalse 可直接用），
    同时携带首次令两个 URL 相等的规则 id/版本。
    """

    __slots__ = ("equal", "rule", "rule_version", "key_a", "key_b")

    def __init__(self, equal, rule, rule_version, key_a, key_b):
        self.equal = equal
        self.rule = rule
        self.rule_version = rule_version
        self.key_a = key_a
        self.key_b = key_b

    def __bool__(self):
        return self.equal

    def __repr__(self):
        if self.equal:
            where = "exact" if self.rule is None else "rule=%s@v%s" % (
                self.rule,
                self.rule_version,
            )
            return "SameResult(equal, %s)" % where
        return "SameResult(not_equal)"


def _strip_fragment(value):
    return value.split("#", 1)[0]


def _trim_trailing_slash(value):
    return value.rstrip("/")


def _lowercase(value):
    return value.lower()


# 内置规则即旧 _key 的三步，顺序与语义保持一致
BUILTIN_RULE_SPECS = (
    ("strip_fragment", _strip_fragment, "去掉 #fragment"),
    ("trim_trailing_slash", _trim_trailing_slash, "去掉结尾斜杠"),
    ("lowercase", _lowercase, "整体转小写"),
)


def builtin_rules():
    return [Rule(rid, fn, 1, desc) for rid, fn, desc in BUILTIN_RULE_SPECS]


def make_strip_query_rule(remove_params, rule_id="strip_tracking_query", version=1):
    """剔除指定查询参数的规则（演示规则升级用）。"""
    remove = set(remove_params)

    def _apply(value):
        if "?" not in value:
            return value
        base, query = value.split("?", 1)
        kept = []
        for pair in query.split("&"):
            if not pair:
                continue
            if pair.split("=", 1)[0] not in remove:
                kept.append(pair)
        return base + ("?" + "&".join(kept) if kept else "")

    return Rule(rule_id, _apply, version, "剔除跟踪参数 %s" % sorted(remove))


class Normalizer:
    """有序规则管道；输出归一化 key、命中规则以及 same 判定。"""

    def __init__(self, rules=None):
        self._rules = list(rules) if rules is not None else builtin_rules()
        ids = [r.id for r in self._rules]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicated rule id: %s" % ids)

    @property
    def rules(self):
        return list(self._rules)

    def versions(self):
        return {r.id: r.version for r in self._rules}

    def normalize(self, url):
        """返回 (key, fired_rule_ids)；fired 记录真正改变过字符串的规则。"""
        value = url or ""
        fired = []
        for rule in self._rules:
            new_value = rule.apply(value)
            if new_value != value:
                fired.append(rule.id)
                value = new_value
        return value, fired

    def match(self, a, b):
        """逐步应用规则，返回首次令两者相等的规则；始终可对未入队 URL 使用。"""
        value_a = a or ""
        value_b = b or ""
        if value_a == value_b:
            return SameResult(True, None, None, value_a, value_b)
        for rule in self._rules:
            value_a = rule.apply(value_a)
            value_b = rule.apply(value_b)
            if value_a == value_b:
                return SameResult(True, rule.id, rule.version, value_a, value_b)
        return SameResult(False, None, None, value_a, value_b)

    def replace_rule(self, rule_id, apply, version=None):
        for i, rule in enumerate(self._rules):
            if rule.id == rule_id:
                new_rule = rule.replaced(apply, version)
                self._rules[i] = new_rule
                return new_rule
        raise KeyError("unknown rule: %s" % rule_id)

    def add_rule(self, rule, position=None):
        if any(r.id == rule.id for r in self._rules):
            raise ValueError("rule id already exists: %s" % rule.id)
        if position is None:
            self._rules.append(rule)
        else:
            self._rules.insert(position, rule)
