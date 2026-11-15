"""对外入口：fingerprint(url) 与 due(url, now) 保持不变，内部走默认 Store。"""
from .rules import RuleSet, normalize, site_of
from .store import Store

_store = Store()


def get_store():
    return _store


def reset():
    """清空全部状态（测试与规则重建用）。"""
    global _store
    _store = Store()


def fingerprint(url):
    return _store.fingerprint(url)


def due(url, now):
    return _store.due(url, now)


def explain(url):
    return _store.explain(url)


def record_fetch(url, now, changed):
    return _store.record_fetch(url, now, changed)


def recanonicalize(site=None):
    return _store.recanonicalize(site)


def pilot_rules(site, ruleset):
    return _store.pilot_rules(site, ruleset)


def rollback_rules(site):
    return _store.rollback_rules(site)


def promote_rules(site):
    return _store.promote_rules(site)


def daily_metrics(site=None):
    return _store.daily_metrics(site)


def alias_alerts(ratio=None, min_aliases=None):
    kw = {}
    if ratio is not None:
        kw["ratio"] = ratio
    if min_aliases is not None:
        kw["min_aliases"] = min_aliases
    return _store.alias_alerts(**kw)


def alias_samples(site, limit=5, per_target=3):
    return _store.alias_samples(site, limit, per_target)
