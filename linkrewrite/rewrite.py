"""对外接口：rewrite(url, site) 与 rules(site)，兼容原签名。

默认注册表内置 shop / news 两组站点配置；需要隔离时可自行构造
:class:`~linkrewrite.registry.Registry` 传给 ``registry`` 参数。
"""

from .engine import analyze_conflicts, apply_rewrite, matched_rules
from .registry import Registry
from .rules import InvalidRule, Rule, rule_from_dict

__all__ = [
    "rewrite",
    "rules",
    "history",
    "get_registry",
    "Registry",
    "Rule",
    "rule_from_dict",
    "InvalidRule",
]


def _build_default_registry():
    registry = Registry()

    # 站点一：shop —— 电商列表页，追踪参数清理 + 分页规范化 + 渠道注入冲突
    registry.configure_site("shop", tracking=["camp", "channel"])
    registry.add_rule(
        "shop",
        Rule(
            id="shop-strip-tracking",
            priority=100,
            description="清理全部追踪参数",
            tracking={"delete": ["utm_source", "utm_medium", "camp", "channel"]},
        ),
    )
    registry.add_rule(
        "shop",
        Rule(
            id="shop-pagination",
            priority=50,
            description="分页参数规范化，注入固定页大小",
            pagination={
                "retain": ["page"],
                "inject": {"page_size": {"value": "20", "min": 1, "max": 100}},
            },
        ),
    )
    # 与 shop-strip-tracking 在 channel 参数上冲突：低优先级，静态分析可见落败
    registry.add_rule(
        "shop",
        Rule(
            id="shop-channel-inject",
            priority=10,
            description="低优先级渠道注入，演示冲突仲裁",
            tracking={"inject": {"channel": {"value": "feed",
                                             "allowed": ["feed", "app"]}}},
        ),
    )

    # 站点二：news —— 资讯站，路径限定规则 + 同优先级注册顺序仲裁
    registry.configure_site("news", pagination=["pg"])
    registry.add_rule(
        "news",
        Rule(
            id="news-article-clean",
            priority=80,
            path=r"^/article/",
            description="文章页只保留分页参数，其余普通参数进白名单",
            tracking={"delete": ["utm_source", "utm_campaign"]},
            pagination={"retain": ["pg"]},
        ),
    )
    registry.add_rule(
        "news",
        Rule(
            id="news-section-keep",
            priority=80,
            path=r"^/section/",
            description="栏目页保留分页与排序",
            pagination={"retain": ["pg"]},
            other={"retain": ["sort"]},
        ),
    )
    # 与 news-article-clean 同优先级竞争 utm_source，注册更晚故落败
    registry.add_rule(
        "news",
        Rule(
            id="news-utm-override",
            priority=80,
            path=r"^/article/",
            description="同优先级注入，演示注册顺序仲裁",
            tracking={"inject": {"utm_source": "newsletter"}},
        ),
    )
    return registry


_DEFAULT_REGISTRY = _build_default_registry()


def get_registry():
    """返回进程级默认注册表（demo 与快速接入使用）。"""
    return _DEFAULT_REGISTRY


def rewrite(url, site, registry=None):
    """按站点规则重写链接。

    返回字典在原有 ``url/site/hit/before/after`` 基础上增加：
    ``conflicts``（本次实际发生的冲突及仲裁解释）、``changes``（按参数类别
    记录的删除/注入/修改前后值）、``matched``、``revision``、``timestamp``、
    ``seq``。未命中任何规则或没有实际变更时 ``after`` 与原 URL 完全一致。
    """
    registry = registry if registry is not None else _DEFAULT_REGISTRY
    return apply_rewrite(registry, url, site)


def rules(site, registry=None):
    """返回站点当前规则、静态冲突解释与修订版本。"""
    registry = registry if registry is not None else _DEFAULT_REGISTRY
    site_rules = registry.get_rules(site)
    return {
        "site": site,
        "rules": [rule.to_dict() for rule in site_rules],
        "conflicts": analyze_conflicts(site_rules),
        "revision": registry.current_revision(site),
    }


def history(site=None, limit=None, registry=None):
    """查询重写记录（含未命中原样返回的记录）。"""
    registry = registry if registry is not None else _DEFAULT_REGISTRY
    return registry.history(site=site, limit=limit)
