"""脱敏策略与血缘解析。

策略严格度从低到高（下游只能取更严格者）::

    NONE(0) < MASK(1) < HASH(2) < REDACT(3)

- NONE：明文，不做任何处理。
- MASK：可逆掩码（如 138***0000），原值由令牌库保管，可受控还原。
- HASH：不可逆假名（HMAC-SHA256 截断），适合联调时做关联键。
- REDACT：直接抹除，原值不落任何存储，无法还原。

血缘是一张有向无环图：边 parent -> child 表示字段从上游表流向下游表。
节点上显式配置的规则沿血缘继承，解析时取所有祖先规则与自身规则的最严
格值；显式配置若比继承值更松，直接抛 PolicyLooseningError。
"""

from .errors import PolicyError, PolicyLooseningError

NONE = "none"
MASK = "mask"
HASH = "hash"
REDACT = "redact"

# 严格度阶梯：数字越大越严格
LEVELS = {NONE: 0, MASK: 1, HASH: 2, REDACT: 3}
# 反向表，用于把解析后的级别还原成策略名
NAMES = {level: name for name, level in LEVELS.items()}

DEFAULT_STRATEGY = MASK

# 节点级默认规则在规则表中的字段键
NODE_DEFAULT = None


def validate_strategy(strategy):
    """校验策略名，非法时抛 PolicyError，合法时原样返回。"""
    if strategy not in LEVELS:
        raise PolicyError("unknown strategy %r, expect one of %s"
                          % (strategy, sorted(LEVELS)))
    return strategy


class Lineage:
    """血缘图 + 节点字段规则，负责解析每个节点的生效策略。"""

    def __init__(self, edges=None, rules=None):
        # node -> set(parent)
        self._parents = {}
        # (node, field) -> 显式策略；节点级默认规则用 field=NODE_DEFAULT
        self._rules = {}
        # node -> {field: strategy}
        self._cache = {}
        if edges:
            for parent, child in edges:
                self.add_edge(parent, child)
        if rules:
            for node, field_rules in rules.items():
                # 规则表里的 None 键即节点级默认策略
                node_default = field_rules.get(NODE_DEFAULT)
                field_rules = {f: s for f, s in field_rules.items()
                               if f is not NODE_DEFAULT}
                self.add_rules(node, field_rules, default=node_default)

    def add_edge(self, parent, child):
        """登记一条血缘边 parent -> child。"""
        if parent == child:
            raise PolicyError("self-loop on node %r is not allowed" % parent)
        self._parents.setdefault(child, set()).add(parent)
        self._parents.setdefault(parent, set())
        self._cache.clear()

    def add_rules(self, node, field_rules=None, default=None):
        """给节点配置字段规则。

        field_rules 为 {字段: 策略}；default 给该节点的未列字段设默认策略。
        配置当下即与继承值比对，松于继承值立刻报错（fail fast）。
        """
        self._parents.setdefault(node, set())
        if field_rules:
            for field, strategy in field_rules.items():
                validate_strategy(strategy)
                self._rules[(node, field)] = strategy
        if default is not None:
            validate_strategy(default)
            self._rules[(node, NODE_DEFAULT)] = default
        self._cache.clear()
        # 立即解析一次，把放松约束的错误在配置阶段就抛出来
        self._resolve(node, frozenset())

    def effective_rules(self, node):
        """解析节点的生效字段规则（含节点级默认），返回 {field: strategy}。

        含 None 键时表示该节点的默认策略。未配置字段不在返回值中，由引擎
        按全局默认策略兜底并登记为遗漏字段。
        """
        return dict(self._resolve(node, frozenset()))

    # ---- 内部实现 -------------------------------------------------------

    def _resolve(self, node, visiting):
        cached = self._cache.get(node)
        if cached is not None:
            return cached
        if node in visiting:
            raise PolicyError(
                "lineage cycle detected at node %r" % node)
        visiting = visiting | {node}

        # 1) 继承值：所有父节点的解析结果取最严格
        inherited = {}
        for parent in self._parents.get(node, ()):
            for field, strategy in self._resolve(parent, visiting).items():
                level = LEVELS[strategy]
                if level > inherited.get(field, -1):
                    inherited[field] = level

        # 2) 自身显式规则：不得放松继承约束，否则只可能持平或更严
        merged = dict(inherited)
        node_default = inherited.get(NODE_DEFAULT)
        for (rule_node, field), strategy in self._rules.items():
            if rule_node != node:
                continue
            level = LEVELS[strategy]
            bound = inherited.get(field)
            if bound is None and field != NODE_DEFAULT:
                # 字段级规则还要受继承来的节点级默认约束
                bound = node_default
            if bound is not None and level < bound:
                raise PolicyLooseningError(
                    "node %r field %r cannot relax inherited strategy %s "
                    "to %s" % (node, field, NAMES[bound], strategy))
            if level > merged.get(field, -1):
                merged[field] = level

        result = {field: NAMES[level] for field, level in merged.items()}
        self._cache[node] = result
        return result
