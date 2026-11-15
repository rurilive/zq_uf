"""重写引擎：参数分类处理、优先级冲突仲裁、重写记录。

处理顺序（每个参数类别 tracking / pagination / other 独立进行）：

1. 取站点规则中路径匹配的规则，按 priority 降序、注册顺序升序排列。
2. 对每个参数，最高优先级规则显式声明的动作（delete / inject / retain）生效，
   低优先级规则的同名动作成为冲突落败方，冲突解释见 :func:`analyze_conflicts`。
3. 任一命中规则声明 retain 即开启白名单模式，各类别白名单取所有命中 retain
   规则声明的并集；未被高优先级规则显式决定、又不在白名单内的参数被删除。
4. 注入参数在校验通过后写入，受同一套优先级仲裁保护。
"""

import copy
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .classifier import CLASSES, classify_param


def matched_rules(registry, site, path):
    """返回站点内路径匹配、按优先级排好序的规则。"""
    return [
        rule
        for rule in registry.get_rules(site)
        if rule.matches_path(path)
    ]


def _mentions(rule):
    """规则显式提到的参数：{cls: {param: action}}。"""
    result = {}
    for cls in CLASSES:
        section = rule.actions.get(cls, {})
        mentions = {}
        for param in section.get("delete", []):
            mentions[param] = "delete"
        for param in section.get("retain", []):
            mentions[param] = "retain"
        for param in section.get("inject", {}):
            mentions[param] = "inject"
        result[cls] = mentions
    return result


def _paths_overlap(rule_a, rule_b):
    """两条路径限定规则是否可能同时命中。

    无路径限定（匹配所有路径）的规则与任何规则重叠；两条正则都锚定到
    ``^/`` 且前缀互斥时判定为不重叠。锚定信息无法静态确定时保守判为重叠。
    """
    patterns = [rule_a.path_re, rule_b.path_re]
    if any(pattern is None for pattern in patterns):
        return True
    anchored = []
    for pattern in patterns:
        text = pattern.pattern
        if text.startswith("^"):
            anchored.append(text[1:])
        else:
            return True  # 非锚定正则无法静态排除重叠
    prefixes = []
    for text in anchored:
        slash = text.find("/", 1)
        prefixes.append(text[: slash + 1] if slash > 0 else text)
    return prefixes[0] == prefixes[1]


def analyze_conflicts(rules):
    """静态冲突分析：同一参数被多条可同时命中的规则显式声明即构成冲突。

    仲裁规则：priority 大的规则胜出；priority 相同时先注册的规则胜出。
    路径互斥（不可能同时命中）的规则不计入冲突。返回的每条冲突都带
    reason，可直接用于排查输出。
    """
    # param -> [ (rule, action) ... ]，按规则优先级顺序
    seen = {}
    for rule in rules:
        for cls, mentions in _mentions(rule).items():
            for param, action in mentions.items():
                seen.setdefault((cls, param), []).append((rule, action))

    conflicts = []
    for (cls, param), entries in sorted(seen.items()):
        # 剔除与胜出规则路径互斥的声明：它们运行时不可能竞争同一 URL
        viable = []
        for rule, action in entries:
            if all(
                _paths_overlap(rule, other)
                for other, _ in entries
                if other is not rule
            ):
                viable.append((rule, action))
        if len(viable) < 2:
            continue
        entries = viable
        winner_rule, winner_action = entries[0]
        losers = [
            {
                "rule": loser_rule.id,
                "priority": loser_rule.priority,
                "action": loser_action,
            }
            for loser_rule, loser_action in entries[1:]
        ]
        priorities = {rule.priority for rule, _ in entries}
        if len(priorities) == 1:
            reason = (
                "参数 %r（%s）被规则 %s 同时声明，优先级均为 %s，"
                "按注册先后由规则 %r 胜出，动作 %s 生效"
                % (
                    param,
                    cls,
                    [rule.id for rule, _ in entries],
                    winner_rule.priority,
                    winner_rule.id,
                    winner_action,
                )
            )
        else:
            reason = (
                "参数 %r（%s）存在竞争：规则 %r(priority=%s, %s) 优先级高于 "
                "%s，规则 %r 的动作 %s 生效，其余声明被忽略"
                % (
                    param,
                    cls,
                    winner_rule.id,
                    winner_rule.priority,
                    winner_action,
                    [
                        "规则 %r(priority=%s, %s)"
                        % (loser.id, loser.priority, action)
                        for loser, action in entries[1:]
                    ],
                    winner_rule.id,
                    winner_action,
                )
            )
        conflicts.append(
            {
                "class": cls,
                "param": param,
                "winner": {
                    "rule": winner_rule.id,
                    "priority": winner_rule.priority,
                    "action": winner_action,
                },
                "losers": losers,
                "reason": reason,
            }
        )
    return conflicts


def _classify_pairs(pairs, extras):
    """把 query 键值对按类别分组，重复参数保留第一次出现的值。"""
    grouped = {cls: {} for cls in CLASSES}
    for name, value in pairs:
        cls = classify_param(
            name,
            tracking_extra=extras["tracking"],
            pagination_extra=extras["pagination"],
        )
        if name not in grouped[cls]:
            grouped[cls][name] = value
    return grouped


def _apply_class(cls, existing, rules, whitelist, top_retain_id):
    """对单个参数类别执行规则，返回 (结果参数, 每规则动作摘要, 变更)。

    :param existing: 该类别现有参数 {name: value}
    :param rules: 已排序、路径匹配的规则
    :param whitelist: 同 URL 命中的全部 retain 规则在该类别的白名单并集；
        None 表示本次重写没有任何 retain 规则（未提及参数原样保留）
    :param top_retain_id: 最高优先级 retain 规则 id，白名单删除归属用
    """
    # 显式仲裁：高优先级规则先占，param -> (rule, action)
    decided = {}
    for rule in rules:
        for param, action in _mentions(rule).get(cls, {}).items():
            decided.setdefault(param, (rule, action))

    # 白名单裁决归属：白名单内的保留记在声明它的最高优先级规则名下
    retain_owner = {}
    for rule in rules:
        for param in rule.actions.get(cls, {}).get("retain", []):
            retain_owner.setdefault(param, rule)

    result = {}
    summary = {rule.id: {"deleted": [], "retained": [], "injected": []}
               for rule in rules}
    removed = {}
    changed = []

    # 1) 处理 URL 中已有的参数
    for param, value in existing.items():
        if param in decided:
            rule, action = decided[param]
            if action == "delete":
                removed[param] = value
                summary[rule.id]["deleted"].append(param)
                continue
            if action == "inject":
                new_value = rule.actions[cls]["inject"][param]["value"]
                result[param] = new_value
                summary[rule.id]["injected"].append(param)
                if new_value != value:
                    changed.append(
                        {"param": param, "before": value, "after": new_value}
                    )
                continue
            # retain
            result[param] = value
            summary[rule.id]["retained"].append(param)
            continue

        if whitelist is not None:
            if param in whitelist:
                result[param] = value
                owner = retain_owner.get(param)
                if owner is not None:
                    summary[owner.id]["retained"].append(param)
            else:
                # 白名单未收录：按白名单语义删除，记在最高优先级 retain 规则名下
                removed[param] = value
                summary[top_retain_id]["deleted"].append(param)
        else:
            # 无白名单：未被任何规则显式提及的参数原样保留
            result[param] = value

    # 2) 处理注入参数（URL 中原本不存在、且未被高优先级 delete 否决的）
    for rule in rules:
        inject = rule.actions.get(cls, {}).get("inject", {})
        for param, spec in inject.items():
            if param in existing:
                continue  # 已在上面的仲裁流程处理
            winner = decided.get(param)
            if winner is not None and winner[0].id != rule.id:
                continue  # 更高优先级的 delete/retain 已否决注入
            result[param] = spec["value"]
            summary[rule.id]["injected"].append(param)

    changes = {
        "removed": removed,
        "injected": {
            param: spec["value"]
            for rule in rules
            for param, spec in rule.actions.get(cls, {}).get("inject", {}).items()
            if param in result and param not in existing
        },
        "changed": changed,
    }
    return result, summary, changes


def apply_rewrite(registry, url, site):
    """执行一次重写并写记录，返回完整结果字典。"""
    parts = urlsplit(url)
    extras = registry.extras(site)
    rules = matched_rules(registry, site, parts.path or "/")
    static_conflicts = analyze_conflicts(rules)

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    grouped = _classify_pairs(pairs, extras)

    # 跨类别白名单：同 URL 命中的任一 retain 规则即开启白名单模式，
    # 各类别的白名单取所有 retain 规则声明的并集
    retain_rules = [
        rule for rule in rules if any(
            "retain" in rule.actions.get(cls, {}) for cls in CLASSES
        )
    ]
    top_retain_id = retain_rules[0].id if retain_rules else None
    whitelists = {}
    for cls in CLASSES:
        if not retain_rules:
            whitelists[cls] = None
        else:
            whitelists[cls] = set()
            for rule in retain_rules:
                whitelists[cls].update(
                    rule.actions.get(cls, {}).get("retain", [])
                )

    merged = {}
    hit_summary = {}
    all_changes = {cls: {"removed": {}, "injected": {}, "changed": []}
                   for cls in CLASSES}
    for cls in CLASSES:
        result, summary, changes = _apply_class(
            cls, grouped[cls], rules, whitelists[cls], top_retain_id
        )
        merged.update(result)
        for rule_id, actions in summary.items():
            hit_summary.setdefault(rule_id, {})[cls] = actions
        all_changes[cls] = changes

    # 仅保留与本 URL 实际相关的冲突（参数存在或被注入）
    relevant_names = set(name for name, _ in pairs) | set(merged)
    conflicts = [
        conflict
        for conflict in static_conflicts
        if conflict["param"] in relevant_names
    ]

    hit = []
    for rule in rules:
        actions = hit_summary.get(rule.id, {})
        hit.append(
            {
                "rule": rule.id,
                "priority": rule.priority,
                "path": rule.path,
                "actions": actions,
            }
        )

    # 按原始 query 的顺序重建，新增参数按名字排序追加，保证输出确定
    ordered_names = list(dict.fromkeys(name for name, _ in pairs))
    for name in sorted(merged):
        if name not in ordered_names:
            ordered_names.append(name)
    new_query = urlencode(
        [(name, merged[name]) for name in ordered_names if name in merged],
        doseq=True,
    )

    changed_any = any(
        all_changes[cls]["removed"]
        or all_changes[cls]["injected"]
        or all_changes[cls]["changed"]
        for cls in CLASSES
    )
    if not changed_any:
        after = url  # 未命中或无实际变更：原样返回，保证幂等
    else:
        after = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
        )

    record = {
        "url": url,
        "site": site,
        "before": url,
        "after": after,
        "hit": hit,
        "conflicts": conflicts,
        "changes": all_changes,
        "matched": bool(rules),
        "revision": registry.current_revision(site),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    record["seq"] = registry.add_record(record)
    return copy.deepcopy(record)
