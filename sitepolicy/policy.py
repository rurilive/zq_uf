"""站点策略：默认 / 分组 / 站点三层继承，字段级冲突记录，原子切换与审计。"""

import copy
import threading
import time

DEFAULT = {"rate": 1, "retry": 3, "timeout": 30}

_LAYERS = ("default", "groups", "sites")

_VALIDATORS = {
    "rate": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0,
    "retry": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
    "timeout": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0,
}

_MISSING = object()

_lock = threading.Lock()
_snapshot = {"default": dict(DEFAULT), "groups": {}, "sites": {}}
_audit = []
_version = 0


class ResolvedPolicy(dict):
    """合并后的策略字典，conflicts 属性携带字段级冲突列表。"""

    def __init__(self, *args, conflicts=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.conflicts = list(conflicts)


def resolve(site, overrides=None):
    """按 默认 -> 分组 -> 站点（-> 临时覆盖）合并，返回 ResolvedPolicy。"""
    snap = _snapshot  # 读当前快照引用，切换对它原子可见
    layers = [("default", snap["default"])]
    site_cfg = snap["sites"].get(site, {})
    group = site_cfg.get("group")
    if group in snap["groups"]:
        layers.append(("group:" + group, snap["groups"][group]))
    layers.append(("site", {k: v for k, v in site_cfg.items() if k != "group"}))
    if overrides:
        layers.append(("override", overrides))

    values = {}
    winner = {}
    sources = {}
    for layer_name, cfg in layers:
        for field, value in cfg.items():
            values[field] = value
            winner[field] = layer_name
            sources.setdefault(field, {})[layer_name] = value

    conflicts = []
    for field in sorted(sources):
        layer_values = sources[field]
        if len({repr(v) for v in layer_values.values()}) > 1:
            conflicts.append({
                "field": field,
                "winner": winner[field],
                "value": values[field],
                "sources": dict(layer_values),
            })
    return ResolvedPolicy(values, conflicts=conflicts)


def update(patch, operator="system"):
    """生成新快照并校验，通过后原子切换；失败保留旧快照并抛 ValueError。"""
    global _snapshot, _version
    layered = _normalize(patch)
    with _lock:
        new_snap = copy.deepcopy(_snapshot)
        changes = []
        for layer, cfg in layered.items():
            if layer == "default":
                _merge_into(new_snap["default"], cfg, "default", changes)
            else:
                for name, fields in cfg.items():
                    bucket = new_snap[layer].setdefault(name, {})
                    _merge_into(bucket, fields, layer[:-1] + ":" + name, changes)
        try:
            _validate(new_snap)
        except ValueError as exc:
            _audit.append({
                "version": _version,
                "ts": _now(),
                "operator": operator,
                "status": "rejected",
                "changes": changes,
                "error": str(exc),
            })
            raise
        _version += 1
        _snapshot = new_snap  # 单次赋值完成原子切换
        _audit.append({
            "version": _version,
            "ts": _now(),
            "operator": operator,
            "status": "applied",
            "changes": changes,
        })
        return {"ok": True, "version": _version, "policy": dict(new_snap["default"])}


def audit():
    """返回全部审计记录（含被拒绝的更新）。"""
    return copy.deepcopy(_audit)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _merge_into(bucket, fields, layer_name, changes):
    for field, value in fields.items():
        before = bucket.get(field, _MISSING)
        bucket[field] = value
        changes.append({
            "layer": layer_name,
            "field": field,
            "before": None if before is _MISSING else before,
            "after": value,
        })


def _normalize(patch):
    """扁平 patch 作用于 default 层；含 default/groups/sites 键时按分层 patch 解析。"""
    if not isinstance(patch, dict):
        raise ValueError("patch must be a dict")
    if any(key in patch for key in _LAYERS):
        unknown = set(patch) - set(_LAYERS)
        if unknown:
            raise ValueError("unknown layer keys: %s" % sorted(unknown))
        layered = {layer: patch.get(layer, {}) for layer in _LAYERS}
    else:
        layered = {"default": patch, "groups": {}, "sites": {}}
    if not isinstance(layered["default"], dict):
        raise ValueError("default layer must be a dict")
    for layer in ("groups", "sites"):
        if not isinstance(layered[layer], dict):
            raise ValueError(layer + " layer must be a dict")
        for name, fields in layered[layer].items():
            if not isinstance(fields, dict):
                raise ValueError("%s[%r] must be a dict" % (layer, name))
    return layered


def _validate(snap):
    def check(fields, where):
        for field, value in fields.items():
            if field == "group":
                continue
            validator = _VALIDATORS.get(field)
            if validator is not None and not validator(value):
                raise ValueError("invalid %s=%r at %s" % (field, value, where))

    check(snap["default"], "default")
    for name, fields in snap["groups"].items():
        check(fields, "group:" + name)
    for name, fields in snap["sites"].items():
        check(fields, "site:" + name)


def _reset():
    """测试用：恢复初始快照并清空审计。"""
    global _snapshot, _version
    with _lock:
        _snapshot = {"default": dict(DEFAULT), "groups": {}, "sites": {}}
        _audit.clear()
        _version = 0
