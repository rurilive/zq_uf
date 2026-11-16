"""字段级日志脱敏。

与旧的"全量正则替换"不同，这里按服务登记字段，每个字段显式指定处理方式：

- ``full``：整段替换为 ``***``，用于手机号、证件号等高敏字段；
- ``partial``：只遮中间部分、保留头尾格式，用于仍需辨认格式的字段；
- ``plain``：已登记但不处理，用于 order_no 这类业务字段，避免被误杀。

规则按服务（record 中的 ``service`` 字段）分开配置并带版本号：每次
:func:`configure` 追加一个新版本，只对之后写入的日志生效；:func:`rollback`
回滚同样以追加版本的方式实现，历史可审计。

未登记字段出现在写入路径上时直接抛出 :class:`UnregisteredFieldError`，
不会带着明文落日志；拒绝事件也会按服务和字段名计入 :func:`report`。
"""

from __future__ import annotations

import copy
import hashlib
import json

FULL = "full"
PARTIAL = "partial"
PLAIN = "plain"
_VALID_MODES = (FULL, PARTIAL, PLAIN)

SERVICE_FIELD = "service"
DEFAULT_SERVICE = "default"

# 每个服务一条单调追加的版本历史，entry 形如
# {"version": int, "rules": {field: {"mode": ..., ...}}, "note": str}
_versions: dict[str, list[dict]] = {}
# 各服务当前生效的规则（最新版本的快照）
_rules: dict[str, dict[str, dict]] = {}

# 各服务统计：calls / masked_fields / unregistered_total / unregistered_breakdown
_stats: dict[str, dict] = {}
# 各服务出现过的关联标识，用于统计去重后的 trace 数
_trace_ids: dict[str, set[str]] = {}


class UnregisteredFieldError(ValueError):
    """record 中出现了该服务未登记的字段，写入被拒绝。"""

    def __init__(self, service: str, fields: list[str]):
        self.service = service
        self.fields = sorted(fields)
        super().__init__(
            f"unregistered fields for service '{service}': {', '.join(self.fields)}"
        )


def _ensure_stats(service: str) -> dict:
    return _stats.setdefault(
        service,
        {
            "calls": 0,
            "masked_fields": 0,
            "unregistered_total": 0,
            "unregistered_breakdown": {},
        },
    )


def _normalize_rules(rules: dict) -> dict[str, dict]:
    """把 "phone" / {"mode": "partial"} 两种简写统一成完整 spec。"""
    if not isinstance(rules, dict):
        raise ValueError("rules must be a dict of field -> spec")
    normalized: dict[str, dict] = {}
    for field, spec in rules.items():
        if not isinstance(field, str) or not field:
            raise ValueError(f"invalid field name: {field!r}")
        if isinstance(spec, str):
            mode, spec = spec, {}
        elif isinstance(spec, dict):
            mode = spec.get("mode")
        else:
            raise ValueError(f"invalid spec for field '{field}': {spec!r}")
        if mode not in _VALID_MODES:
            raise ValueError(
                f"field '{field}' has invalid mode {mode!r}, "
                f"expected one of {_VALID_MODES}"
            )
        entry = {"mode": mode}
        if mode == PARTIAL:
            keep_head = int(spec.get("keep_head", 3))
            keep_tail = int(spec.get("keep_tail", 4))
            if keep_head < 0 or keep_tail < 0:
                raise ValueError(
                    f"field '{field}': keep_head/keep_tail must be non-negative"
                )
            entry["keep_head"] = keep_head
            entry["keep_tail"] = keep_tail
        normalized[field] = entry
    return normalized


def configure(service: str, rules: dict, note: str = "") -> int:
    """登记（或更新）某个服务的字段规则，返回新版本号。

    规则全量替换该服务的旧规则并追加一个版本；已经处理过的日志保持当时
    的版本不变，新版本只对之后的 :func:`mask` 调用生效。
    """
    if not isinstance(service, str) or not service:
        raise ValueError("service must be a non-empty string")
    normalized = _normalize_rules(rules)
    history = _versions.setdefault(service, [])
    version = len(history) + 1
    history.append(
        {
            "version": version,
            "rules": copy.deepcopy(normalized),
            "note": note or "",
        }
    )
    _rules[service] = normalized
    _ensure_stats(service)
    return version


def rollback(service: str, version: int | None = None) -> int:
    """按服务回滚规则。

    - ``version`` 给定时回滚到该版本；不传时回滚到上一个版本；
    - 回滚以"把目标版本的规则追加为新版本"的方式实现，版本号只增不减。
    """
    history = _versions.get(service)
    if not history:
        raise ValueError(f"no rules registered for service '{service}'")
    if version is None:
        if len(history) < 2:
            raise ValueError(
                f"service '{service}' has only v1, nothing to roll back to"
            )
        target = len(history) - 1
    else:
        target = version
    if not isinstance(target, int) or not 1 <= target <= len(history):
        raise ValueError(
            f"unknown version {version} for service '{service}'"
        )
    snapshot = copy.deepcopy(history[target - 1]["rules"])
    new_version = len(history) + 1
    history.append(
        {
            "version": new_version,
            "rules": snapshot,
            "note": f"rollback to v{target}",
        }
    )
    _rules[service] = copy.deepcopy(snapshot)
    return new_version


def versions(service: str) -> list[dict]:
    """返回某服务的版本历史（不含规则明细，避免把配置扩散到查询路径）。"""
    return [
        {"version": entry["version"], "note": entry["note"]}
        for entry in _versions.get(service, [])
    ]


def _partial(text: str, keep_head: int, keep_tail: int) -> str:
    """保留头尾、遮蔽中间；短到无法保留格式时退化为整段遮蔽。"""
    if len(text) <= keep_head + keep_tail:
        return "***"
    head = text[:keep_head]
    tail = text[len(text) - keep_tail :] if keep_tail else ""
    return head + "*" * (len(text) - keep_head - keep_tail) + tail


def _apply_spec(name: str, value, spec: dict):
    mode = spec["mode"]
    if mode == PLAIN:
        return value, None
    if isinstance(value, (dict, list)):
        # 结构化值没有可靠的标量遮蔽方式，直接报错而不是 str() 后漏出内容
        raise TypeError(
            f"field '{name}' has unsupported type {type(value).__name__}"
        )
    text = value if isinstance(value, str) else str(value)
    if mode == FULL:
        return "***", FULL
    return _partial(text, spec["keep_head"], spec["keep_tail"]), PARTIAL


def _trace_id(service: str, record: dict) -> str:
    """基于原始 record 生成稳定关联标识，与规则版本无关。"""
    payload = json.dumps(
        {"service": service, "record": record},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return "tid_" + digest[:16]


def _record_unregistered(service: str, fields: list[str]) -> None:
    stats = _ensure_stats(service)
    stats["unregistered_total"] += len(fields)
    breakdown = stats["unregistered_breakdown"]
    for name in fields:
        breakdown[name] = breakdown.get(name, 0) + 1


def mask(record: dict) -> dict:
    """对一条日志做字段级脱敏，返回脱敏结果与处理明细。

    未登记字段直接抛 :class:`UnregisteredFieldError`（字段名在异常里列出），
    调用方应当跳过写入而不是落明文。成功时返回::

        {
          "service": 服务名,
          "version": 本次使用的规则版本,
          "record": 脱敏后的日志（新 dict，不改入参）,
          "masked": [{"field": ..., "mode": ..., "value": ...}],
          "unregistered": [],
          "trace_id": 稳定关联标识,
        }
    """
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")

    service = record.get(SERVICE_FIELD, DEFAULT_SERVICE)
    rules = _rules.get(service)
    if rules is None:
        unknown = [name for name in record if name != SERVICE_FIELD]
        _record_unregistered(service, unknown)
        raise UnregisteredFieldError(service, unknown)

    unknown = [
        name for name in record if name != SERVICE_FIELD and name not in rules
    ]
    if unknown:
        _record_unregistered(service, unknown)
        raise UnregisteredFieldError(service, unknown)

    cleaned = {}
    masked_fields = []
    for name, value in record.items():
        if name == SERVICE_FIELD:
            cleaned[name] = value
            continue
        new_value, mode = _apply_spec(name, value, rules[name])
        cleaned[name] = new_value
        if mode is not None:
            masked_fields.append(
                {"field": name, "mode": mode, "value": new_value}
            )

    stats = _ensure_stats(service)
    stats["calls"] += 1
    stats["masked_fields"] += len(masked_fields)

    trace_id = _trace_id(service, record)
    _trace_ids.setdefault(service, set()).add(trace_id)

    return {
        "service": service,
        "version": _versions[service][-1]["version"],
        "record": cleaned,
        "masked": masked_fields,
        "unregistered": [],
        "trace_id": trace_id,
    }


def report() -> dict:
    """按服务汇总脱敏情况，供安全同学查看。

    每个服务给出当前规则版本、成功处理条数、实际遮蔽字段数、未登记字段
    总数及按字段名的明细（便于补规则）、去重后的关联标识数。
    """
    services = {}
    total_masked = 0
    total_unregistered = 0
    all_services = set(_stats) | set(_trace_ids) | set(_versions)
    for service in sorted(all_services):
        stats = _stats.get(
            service,
            {
                "calls": 0,
                "masked_fields": 0,
                "unregistered_total": 0,
                "unregistered_breakdown": {},
            },
        )
        history = _versions.get(service, [])
        entry = {
            "version": history[-1]["version"] if history else None,
            "calls": stats["calls"],
            "masked_fields": stats["masked_fields"],
            "unregistered_fields": stats["unregistered_total"],
            "unregistered_breakdown": dict(
                sorted(stats["unregistered_breakdown"].items())
            ),
            "trace_ids": len(_trace_ids.get(service, set())),
        }
        services[service] = entry
        total_masked += entry["masked_fields"]
        total_unregistered += entry["unregistered_fields"]

    return {
        "services": services,
        "masked": total_masked,
        "unregistered": total_unregistered,
        "trace_ids": sum(len(ids) for ids in _trace_ids.values()),
    }


def reset() -> None:
    """清空规则与统计，主要给测试和演示使用。"""
    _versions.clear()
    _rules.clear()
    _stats.clear()
    _trace_ids.clear()
