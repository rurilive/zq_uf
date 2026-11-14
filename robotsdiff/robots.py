"""robots 规则差异分析、定向缓存失效、审计与回滚。

纯 Python 标准库实现。规则形如::

    {"allow": ["/pub"], "disallow": ["/private"]}

段内为路径前缀，按 ``path.startswith(prefix)`` 命中；allow 命中优先于
disallow，均未命中默认放行，空串前缀忽略。
"""

import copy
import hashlib
from datetime import datetime, timezone

# host -> 当前生效规则（规范化后的 {"allow": [...], "disallow": [...]}）
_rules = {}
# host -> {path: allowed 判定结果}
_cache = {}
# host -> 最近一次变更的差异分类
_diffs = {}
# host -> 审计记录（append-only，记录内的规则快照同时作为回滚历史）
_audit = {}

_EMPTY_DIFF = {
    "newly_disallowed": [],
    "lifted_disallowed": [],
    "newly_allowed": [],
    "removed_allow": [],
    "affected_paths": [],
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _normalize(rules):
    """规范化规则：只保留 allow/disallow，去空串、去重、排序。"""
    rules = rules or {}
    return {
        "allow": sorted({p for p in (rules.get("allow") or []) if p}),
        "disallow": sorted({p for p in (rules.get("disallow") or []) if p}),
    }


def _evaluate(rules, path):
    """按给定规则判定 path 是否放行（不读写缓存）。"""
    if any(path.startswith(prefix) for prefix in rules.get("allow") or []):
        return True
    if any(path.startswith(prefix) for prefix in rules.get("disallow") or []):
        return False
    return True


def _summary(rules):
    """规则摘要：段条数 + 内容 sha256，便于审计回溯。"""
    payload = "allow:{a}|disallow:{d}".format(
        a=",".join(rules["allow"]),
        d=",".join(rules["disallow"]),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return "allow={a} disallow={d} sha256={h}".format(
        a=len(rules["allow"]),
        d=len(rules["disallow"]),
        h=digest,
    )


def _source_meta(source, fetched_at, summary, rules):
    """整理规则来源信息；source 可为字符串，也可为携带来源字段的 dict。"""
    if isinstance(source, dict):
        return {
            "source": source.get("source") or source.get("url"),
            "fetched_at": source.get("fetched_at") or fetched_at or _now(),
            "summary": source.get("summary") or summary or _summary(rules),
        }
    return {
        "source": source,
        "fetched_at": fetched_at or _now(),
        "summary": summary or _summary(rules),
    }


def _diff_rules(old, new):
    """前缀级差异分类，受影响路径由调用方结合缓存填充。"""
    old_allow, new_allow = set(old["allow"]), set(new["allow"])
    old_dis, new_dis = set(old["disallow"]), set(new["disallow"])
    return {
        "newly_disallowed": sorted(new_dis - old_dis),
        "lifted_disallowed": sorted(old_dis - new_dis),
        "newly_allowed": sorted(new_allow - old_allow),
        "removed_allow": sorted(old_allow - new_allow),
        "affected_paths": [],
    }


def _apply_change(host, old, new, diff, record):
    """公共收尾：按差异定向失效缓存，切换规则并写审计。"""
    host_cache = _cache.setdefault(host, {})
    affected = sorted(
        path
        for path in host_cache
        if _evaluate(old, path) != _evaluate(new, path)
    )
    diff["affected_paths"] = affected
    for path in affected:
        del host_cache[path]

    _rules[host] = new
    _diffs[host] = copy.deepcopy(diff)
    record.update(
        {
            "time": _now(),
            "host": host,
            "diff": copy.deepcopy(diff),
            "invalidation": {
                "affected": list(affected),
                "invalidated": len(affected),
                "kept": len(host_cache),
            },
            "rules": copy.deepcopy(new),
        }
    )
    _audit.setdefault(host, []).append(record)
    return diff


def update(host, new_rules, source=None, fetched_at=None, summary=None):
    """更新规则：差异分析 + 定向失效 + 审计，返回生效中的规则副本。

    source 为规则来源（如 robots.txt 的 URL），fetched_at 为抓取时间，
    summary 可覆盖自动生成的规则摘要。
    """
    old = _rules.get(host) or _normalize(None)
    new = _normalize(new_rules)
    diff = _diff_rules(old, new)
    meta = _source_meta(source, fetched_at, summary, new)
    _apply_change(
        host,
        old,
        new,
        diff,
        {
            "action": "update",
            "source": meta["source"],
            "fetched_at": meta["fetched_at"],
            "summary": meta["summary"],
        },
    )
    return copy.deepcopy(new)


def rollback(host):
    """回滚到上一版规则，返回本次回滚的差异分类。

    回滚同样执行差异分析与定向失效，并追加 action 为 rollback 的审计
    记录，记录携带被恢复版本的来源与抓取时间。
    """
    records = _audit.get(host)
    if not records:
        raise KeyError("no rule history for host: {0!r}".format(host))

    old = _rules.get(host) or _normalize(None)
    last = records[-1]
    if len(records) >= 2:
        target_record = records[-2]
        new = _normalize(target_record["rules"])
        provenance = {
            "source": target_record["source"],
            "fetched_at": target_record["fetched_at"],
            "summary": target_record["summary"],
        }
        rollback_to = len(records) - 1
    else:
        # 仅有一次更新时，回滚到初始状态（无规则、默认放行）；
        # 来源与抓取时间沿用唯一一版规则的登记，摘要按空规则重算。
        new = _normalize(None)
        provenance = {
            "source": last["source"],
            "fetched_at": last["fetched_at"],
            "summary": _summary(new),
        }
        rollback_to = None

    diff = _diff_rules(old, new)
    _apply_change(
        host,
        old,
        new,
        diff,
        {
            "action": "rollback",
            "source": provenance["source"],
            "fetched_at": provenance["fetched_at"],
            "summary": provenance["summary"],
            "rollback_to": rollback_to,
        },
    )
    return copy.deepcopy(diff)


def allowed(host, path):
    """判定 path 是否放行，结果按路径缓存，规则变更后定向失效。"""
    host_cache = _cache.setdefault(host, {})
    if path not in host_cache:
        host_cache[path] = _evaluate(
            _rules.get(host) or _normalize(None), path
        )
    return host_cache[path]


def affected(host):
    """最近一次变更中判定翻转的已缓存路径（定向失效清单），无历史为空。"""
    return list(_diffs.get(host, {}).get("affected_paths", []))


def diff(host):
    """最近一次变更的完整差异分类，从未更新过的 host 各字段均为空。"""
    current = _diffs.get(host, _EMPTY_DIFF)
    return {key: list(value) for key, value in current.items()}


def audit(host):
    """返回 host 的审计记录副本（append-only），无历史为空。"""
    return copy.deepcopy(_audit.get(host, []))


def cache(host):
    """返回 host 当前路径判定缓存的副本。"""
    return dict(_cache.get(host, {}))


def _reset():
    """清空全部内存状态，主要供测试隔离使用。"""
    _rules.clear()
    _cache.clear()
    _diffs.clear()
    _audit.clear()
