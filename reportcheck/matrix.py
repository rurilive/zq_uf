"""接口检测覆盖登记。

登记单元是 ``(接口, 环境)``，同一接口在测试环境和线上的结论分开保存，
互不混用；应测的攻击类型属于接口本身，各环境共用同一份清单。

每个攻击类型独立记录结论，多次登记按类型合并，更新一个类型不会清掉
同一接口其他类型的结果。结论分四类：

* ``hit`` / ``miss``：已执行，计入已覆盖；
* ``skip``：显式跳过，原因需归入权限不足、环境不支持、没排期；
* 未给出结论（或显式 ``untested``）：计入未测。

版本变化时沿用接口的应测类型，保证前后同一口径；若新版本已覆盖比例
低于上一版本，``coverage_drop`` 会单独给出提示，补测后提示消失。
"""

from __future__ import annotations

import copy

SKIP_PERMISSION = "permission"
SKIP_ENVIRONMENT = "environment"
SKIP_NOT_SCHEDULED = "not_scheduled"

SKIP_REASON_LABELS = {
    SKIP_PERMISSION: "权限不足",
    SKIP_ENVIRONMENT: "环境不支持",
    SKIP_NOT_SCHEDULED: "没排期",
}

_SKIP_REASON_ALIASES = {
    "permission": SKIP_PERMISSION,
    "no_permission": SKIP_PERMISSION,
    "permission_denied": SKIP_PERMISSION,
    "insufficient_permission": SKIP_PERMISSION,
    "no_access": SKIP_PERMISSION,
    "权限不足": SKIP_PERMISSION,
    "无权限": SKIP_PERMISSION,
    "environment": SKIP_ENVIRONMENT,
    "env": SKIP_ENVIRONMENT,
    "environment_unsupported": SKIP_ENVIRONMENT,
    "unsupported_environment": SKIP_ENVIRONMENT,
    "env_unsupported": SKIP_ENVIRONMENT,
    "环境不支持": SKIP_ENVIRONMENT,
    "环境不满足": SKIP_ENVIRONMENT,
    "not_scheduled": SKIP_NOT_SCHEDULED,
    "schedule": SKIP_NOT_SCHEDULED,
    "unscheduled": SKIP_NOT_SCHEDULED,
    "no_schedule": SKIP_NOT_SCHEDULED,
    "no_plan": SKIP_NOT_SCHEDULED,
    "没排期": SKIP_NOT_SCHEDULED,
    "未排期": SKIP_NOT_SCHEDULED,
    "排期不足": SKIP_NOT_SCHEDULED,
}

_HIT_ALIASES = {"hit", "found", "vulnerable", "命中", "命中漏洞"}
_MISS_ALIASES = {
    "miss",
    "clean",
    "pass",
    "passed",
    "safe",
    "not_hit",
    "no_hit",
    "未命中",
    "无命中",
}
_SKIP_ALIASES = {"skip", "skipped", "跳过"}
_UNTESTED_ALIASES = {
    "untested",
    "not_tested",
    "not_run",
    "pending",
    "todo",
    "none",
    "",
    "未测",
    "未测试",
}

_EXPECTED_KEYS = ("expected", "should_test", "attack_types", "all_types")
_SKIP_REASON_KEYS = ("skip_reasons", "skip_reason")

# (接口名, 环境) -> 登记记录。每个环境保留版本历史，用于比对覆盖下降。
_records = {}
# 接口名 -> 应测类型清单（按首次声明顺序，只增不减）。
_catalog = {}
_seen_pairs = set()


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("缺少%s" % field)
    return value.strip()


def _dedupe(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _norm_reason(value):
    if value is None:
        return None
    token = str(value).strip().lower()
    if not token:
        return None
    reason = _SKIP_REASON_ALIASES.get(token)
    if reason is None:
        allowed = "、".join(SKIP_REASON_LABELS.values())
        raise ValueError("跳过原因需要是%s之一，收到 %r" % (allowed, value))
    return reason


def _norm_outcome(value):
    """把一条类型结果规范成 ``(结果, 原始跳过原因)``。

    结果只有 ``hit`` / ``miss`` / ``skip`` / ``untested`` 四种。字典形式
    可以顺带携带 ``reason``，字符串也支持 ``"skip:permission"`` 的写法。
    """
    if value is None:
        return "untested", None
    if isinstance(value, dict):
        raw = value.get("result", value.get("status", value.get("outcome")))
        reason = value.get("reason", value.get("skip_reason"))
        if raw is None:
            raise ValueError("执行结果缺少 result/status 字段")
    elif isinstance(value, str):
        raw = value
        reason = None
        if raw.strip().lower().startswith("skip:"):
            raw, reason = raw.split(":", 1)
    else:
        raise ValueError("执行结果需要字符串或字典，收到 %r" % (value,))

    token = str(raw).strip().lower()
    if token in _HIT_ALIASES:
        return "hit", None
    if token in _MISS_ALIASES:
        return "miss", None
    if token in _SKIP_ALIASES:
        return "skip", reason
    if token in _UNTESTED_ALIASES:
        return "untested", None
    raise ValueError("未知执行结果: %r" % (value,))


def _as_type_list(value, field):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value)
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("%s需要类型列表" % field)
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("%s里存在非法类型: %r" % (field, item))
        result.append(item.strip())
    return result


def _parse_api(api):
    if not isinstance(api, dict):
        raise ValueError("api 需要字典")
    name = _text(api.get("name"), "接口名")
    environment = api.get("environment")
    if environment is not None:
        environment = _text(environment, "environment")
    version = api.get("version")
    if version is not None:
        version = str(version).strip()

    declared = []
    for key in _EXPECTED_KEYS:
        if key in api and api[key] is not None:
            declared.extend(_as_type_list(api[key], key))
            break

    entries = {}
    raw_types = api.get("types")
    if raw_types is None:
        raw_types = {}
    if isinstance(raw_types, dict):
        entries.update(raw_types)
    elif isinstance(raw_types, (list, tuple, set, frozenset)):
        declared.extend(_as_type_list(raw_types, "types"))
    else:
        raise ValueError("types 需要字典或类型列表")

    # 单独声明的跳过原因映射，例如 {"ssrf": "permission"}。
    skip_reasons = {}
    for key in _SKIP_REASON_KEYS:
        raw = api.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            skip_reasons.update({k: v for k, v in raw.items() if v is not None})
        elif key == "skip_reason":
            # 单个原因作用于所有没有内联原因的 skip。
            skip_reasons["__all__"] = raw
    return name, environment, version, declared, entries, skip_reasons


def _new_record(name, environment):
    return {
        "api": name,
        "environment": environment,
        "versions": [],
        "pending": set(),
    }


def _new_version_state(version):
    return {"version": version, "expected": [], "results": {}, "skips": {}}


def _get_version_state(record, version):
    """取出本次登记对应的版本状态；版本变化时新建并沿用应测清单。"""
    versions = record["versions"]
    if not versions:
        state = _new_version_state(version)
        versions.append(state)
        return state, False
    if version is None or versions[-1]["version"] == version:
        # 没写版本视为继续当前版本，避免把同一轮登记误判成新版本。
        return versions[-1], False
    state = _new_version_state(version)
    versions.append(state)
    return state, True


def _sync_latest(record, catalog):
    """让最新版本的应测清单跟上接口清单，历史版本保持原样。"""
    state = record["versions"][-1]
    for kind in catalog:
        if kind not in state["expected"]:
            state["expected"].append(kind)


def _is_resolved(state, kind):
    return kind in state["results"] or kind in state["skips"]


def _covered_types(state, types):
    return [kind for kind in types if kind in state["results"]]


def _coverage_on(state, types):
    """按给定类型口径计算覆盖率，保证不同版本可比。"""
    if not types:
        return 0.0
    return len(_covered_types(state, types)) / len(types)


def _coverage_drop(record):
    """上线后覆盖相对历史最佳水平下降时，给出单独的下降提示。

    所有版本都按当前版本的应测类型计算，口径一致，扩类型不会造成假下降；
    对比的是历史最佳版本而不是紧邻的上一版，这样连续两版都停在低位时仍
    会持续提示，直到补测把覆盖拉回同一口径。
    """
    versions = record["versions"]
    if len(versions) < 2:
        return None
    current = versions[-1]
    current_types = current["expected"]
    if not current_types:
        return None

    previous = max(versions[:-1], key=lambda item: _coverage_on(item, current_types))
    previous_ratio = _coverage_on(previous, current_types)
    current_ratio = _coverage_on(current, current_types)
    if current_ratio >= previous_ratio:
        return None

    lost = [
        kind
        for kind in _covered_types(previous, current_types)
        if kind not in current["results"]
    ]
    if not lost:
        return None
    return {
        "api": record["api"],
        "environment": record["environment"],
        "from_version": previous["version"],
        "to_version": current["version"],
        "from_covered": len(_covered_types(previous, current_types)),
        "to_covered": len(_covered_types(current, current_types)),
        "total": len(current_types),
        "from_coverage": previous_ratio,
        "to_coverage": current_ratio,
        "lost_types": lost,
    }


def _snapshot(record):
    state = record["versions"][-1]
    expected = list(state["expected"])
    results = state["results"]
    skips = state["skips"]

    covered = [
        {"type": kind, "result": results[kind]} for kind in expected if kind in results
    ]
    skipped = []
    for kind in expected:
        if kind not in skips or kind in results:
            continue
        code = skips[kind]
        label = SKIP_REASON_LABELS.get(code, "未说明")
        skipped.append(
            {
                "type": kind,
                "result": "skip",
                "reason": code,
                "reason_code": code,
                "reason_label": label,
                "reason_text": label,
                "reason_missing": code is None,
            }
        )

    missing = [k for k in expected if not _is_resolved(state, k)]
    pending = [
        k for k in expected if k in record["pending"] and not _is_resolved(state, k)
    ]

    total = len(expected)
    ratio = (len(covered) / total) if total else 0.0
    drop = _coverage_drop(record)
    return {
        "api": record["api"],
        "environment": record["environment"],
        "version": state["version"],
        "expected": expected,
        "total": total,
        "covered": covered,
        "covered_count": len(covered),
        "coverage": ratio,
        "missing": missing,
        "missing_count": len(missing),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "skip_reasons": {
            item["type"]: item["reason_label"]
            for item in skipped
            if item["reason"] is not None
        },
        "pending_new": pending,
        "coverage_drop": drop,
        "dropped": drop is not None,
    }


def coverage(api):
    """登记一个接口的检测覆盖，返回该接口当前的覆盖结论。

    接口描述至少包含 ``name``；``environment`` 用于区分测试环境和线上，
    不同环境各占一行结论。``types`` 是攻击类型到执行结果的映射，也可以用
    ``expected`` 单独声明应测但还没跑的类型。多次调用按类型合并：同一
    接口各类型的结论互不覆盖。
    """
    name, environment, version, declared, entries, skip_reasons = _parse_api(api)
    # 先把所有类型结果解析完再落状态，非法输入不会留下半截登记。
    parsed = []
    for raw_kind, value in entries.items():
        kind = str(raw_kind).strip()
        if not kind:
            raise ValueError("攻击类型不能为空")
        outcome, inline_reason = _norm_outcome(value)
        reason = None
        if outcome == "skip":
            raw_reason = inline_reason
            if raw_reason is None:
                raw_reason = skip_reasons.get(kind, skip_reasons.get("__all__"))
            # 没写原因也照样登记为跳过，原因留空并在 report 里单独提示，
            # 不替安全同学猜一个“没排期”。
            reason = _norm_reason(raw_reason)
        parsed.append((kind, outcome, reason))

    key = (name, environment)
    is_new_api = name not in _catalog
    is_new_pair = key not in _seen_pairs

    catalog = _catalog.setdefault(name, [])
    catalog_before = set(catalog)
    declared_types = _dedupe(declared + [kind for kind, _, _ in parsed])
    new_types = [kind for kind in declared_types if kind not in catalog_before]
    catalog.extend(new_types)

    record = _records.get(key)
    if record is None:
        record = _new_record(name, environment)
        _records[key] = record

    state, version_changed = _get_version_state(record, version)
    # 其他环境共用接口的应测清单，新类型在各环境分别进入待测。
    for other in _records.values():
        if other["api"] != name:
            continue
        _sync_latest(other, catalog)
        other_state = other["versions"][-1]
        for kind in new_types:
            if not _is_resolved(other_state, kind):
                other["pending"].add(kind)

    untested_declared = []
    for kind, outcome, reason in parsed:
        if outcome in ("hit", "miss"):
            state["results"][kind] = outcome
            state["skips"].pop(kind, None)
            record["pending"].discard(kind)
        elif outcome == "skip":
            state["results"].pop(kind, None)
            state["skips"][kind] = reason
            record["pending"].discard(kind)
        else:  # untested
            state["results"].pop(kind, None)
            state["skips"].pop(kind, None)
            untested_declared.append(kind)

    # 新接口、新环境、新版本或新声明的类型进入待测清单；补测后自动移出。
    seed = list(state["expected"]) if (version_changed or is_new_pair) else new_types
    seed.extend(untested_declared)
    for kind in seed:
        if kind in state["expected"] and not _is_resolved(state, kind):
            record["pending"].add(kind)
    for kind in list(record["pending"]):
        if kind not in state["expected"] or _is_resolved(state, kind):
            record["pending"].discard(kind)

    _seen_pairs.add(key)
    snapshot = _snapshot(record)
    snapshot["new_api"] = is_new_api
    return copy.deepcopy(snapshot)


def report(api=None):
    """按接口查看覆盖情况，``api`` 为空时返回全部接口。

    每个 ``(接口, 环境)`` 一行；顶层另外汇总新增待测和覆盖下降，评审
    可以直接看到哪些接口、哪些类型还没有覆盖。
    """
    if api is not None:
        api = _text(api, "接口名")

    rows = []
    pending_new = []
    coverage_drops = []
    skips_without_reason = []
    for (name, _environment), record in _records.items():
        if api is not None and name != api:
            continue
        snapshot = _snapshot(record)
        rows.append(copy.deepcopy(snapshot))
        if snapshot["pending_new"]:
            pending_new.append(
                {
                    "api": name,
                    "environment": snapshot["environment"],
                    "version": snapshot["version"],
                    "types": list(snapshot["pending_new"]),
                }
            )
        if snapshot["coverage_drop"] is not None:
            coverage_drops.append(snapshot["coverage_drop"])
        for item in snapshot["skipped"]:
            if item["reason_missing"]:
                skips_without_reason.append(
                    {
                        "api": name,
                        "environment": snapshot["environment"],
                        "type": item["type"],
                    }
                )

    return {
        "api": api,
        "count": len(rows),
        "rows": rows,
        "pending_new": pending_new,
        "pending_new_apis": _dedupe([item["api"] for item in pending_new]),
        "coverage_drops": coverage_drops,
        "drops": coverage_drops,
        "skips_without_reason": skips_without_reason,
    }


def _reset():
    """清空登记状态，供测试或新的评估轮次使用。"""
    _records.clear()
    _catalog.clear()
    _seen_pairs.clear()
