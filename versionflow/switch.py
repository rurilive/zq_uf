"""可编排的数据集版本切换：停写、切目录、更新元数据、清缓存。

switch(dataset, version) 按固定顺序执行四步流水线，每步都有补偿动作；
任一步失败时按逆序回退已完成步骤，版本恢复到上一版，并抛出 SwitchError。
操作人、起止版本和原因写入审计日志（audit_log），不塞进返回字典。
"""

import json
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

PIPELINE_STEPS = ("stop_writes", "switch_dir", "update_metadata", "clear_cache")

__all__ = [
    "PIPELINE_STEPS",
    "Step",
    "SwitchError",
    "audit_log",
    "cache_keys",
    "current",
    "default_pipeline",
    "metadata",
    "put_cache",
    "register_dataset",
    "register_version",
    "reset",
    "switch",
]


class SwitchError(RuntimeError):
    """切换流水线执行失败；已完成步骤已按逆序回退。"""

    def __init__(self, message, *, dataset, version, step, step_results):
        super().__init__(message)
        self.dataset = dataset
        self.version = version
        self.step = step
        self.step_results = step_results


class Step:
    """一个可执行、可回退的流水线步骤。"""

    def __init__(self, name, action, compensate):
        self.name = name
        self.action = action
        self.compensate = compensate


class _Dataset:
    def __init__(self, name, root, versions=(), current=None, strict=False):
        self.name = name
        self.root = Path(root)
        self.strict = strict
        self.versions = set(versions)
        self.current = current
        self.writes_stopped = False
        self.cache = {}


_lock = threading.RLock()
_datasets = {}
_audit = []
_store_root = None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_name(kind, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{kind} 必须是非空字符串")
    if not _NAME_RE.match(name):
        raise ValueError(f"{kind} 含非法字符：{name!r}（只允许 [A-Za-z0-9_.-]）")


def _ensure_store_root():
    global _store_root
    if _store_root is None:
        _store_root = Path(tempfile.mkdtemp(prefix="versionflow-"))
    return _store_root


def _add_version(ds, version):
    (ds.root / version).mkdir(parents=True, exist_ok=True)
    ds.versions.add(version)


def _write_pointer(ds, version):
    (ds.root / "CURRENT").write_text(version, encoding="utf-8")


def _write_metadata(ds, **fields):
    payload = {"updated_at": _now()}
    payload.update(fields)
    (ds.root / "metadata.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _get_or_create(name):
    ds = _datasets.get(name)
    if ds is None:
        ds = _Dataset(name, _ensure_store_root() / name)
        ds.root.mkdir(parents=True, exist_ok=True)
        _datasets[name] = ds
    return ds


def reset():
    """清空数据集、审计日志和存储目录，回到初始状态。"""
    global _store_root
    with _lock:
        _datasets.clear()
        _audit.clear()
        if _store_root is not None:
            shutil.rmtree(_store_root, ignore_errors=True)
            _store_root = None


def register_dataset(name, versions=(), current=None):
    """注册数据集及可用版本；注册后版本集合固定（strict）。"""
    with _lock:
        _validate_name("dataset", name)
        if name in _datasets:
            raise ValueError(f"数据集已注册：{name}")
        for version in versions:
            _validate_name("version", version)
        if current is not None and current not in versions:
            raise ValueError(f"当前版本 {current} 不在已注册版本 {sorted(versions)} 中")
        ds = _Dataset(name, _ensure_store_root() / name, versions, current, strict=True)
        ds.root.mkdir(parents=True, exist_ok=True)
        for version in versions:
            _add_version(ds, version)
        if current is not None:
            _write_pointer(ds, current)
            _write_metadata(
                ds,
                current_version=current,
                previous_version=None,
                operator="system",
                reason="register_dataset",
            )
        _datasets[name] = ds


def register_version(dataset, version):
    """给已有数据集追加一个版本；数据集不存在则按非 strict 创建。"""
    with _lock:
        _validate_name("version", version)
        ds = _get_or_create(dataset)
        if ds.strict:
            raise ValueError(f"数据集 {dataset} 版本集合已固定，不能追加 {version}")
        _add_version(ds, version)


def current(dataset):
    ds = _datasets.get(dataset)
    return ds.current if ds is not None else None


def metadata(dataset):
    ds = _datasets.get(dataset)
    if ds is None:
        raise ValueError(f"未知数据集：{dataset}")
    path = ds.root / "metadata.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def put_cache(dataset, key, value, version=None):
    """写入下游缓存，按版本打标；版本缺省取数据集当前版本。"""
    with _lock:
        ds = _datasets.get(dataset)
        if ds is None:
            raise ValueError(f"未知数据集：{dataset}")
        ds.cache[key] = {"version": version if version is not None else ds.current, "value": value}


def cache_keys(dataset):
    ds = _datasets.get(dataset)
    if ds is None:
        raise ValueError(f"未知数据集：{dataset}")
    return sorted(ds.cache)


def audit_log():
    """审计记录：操作人、起止版本、原因、状态和每步结果。"""
    return [dict(entry) for entry in _audit]


# ---- 流水线步骤：action 返回细节，compensate 负责回退 ----

def _act_stop_writes(ds, ctx):
    ds.writes_stopped = True
    return "写入已暂停"


def _undo_stop_writes(ds, ctx, detail):
    ds.writes_stopped = False


def _act_switch_dir(ds, ctx):
    pointer = ds.root / "CURRENT"
    ctx["scratch"]["previous"] = pointer.read_text(encoding="utf-8") if pointer.exists() else None
    _write_pointer(ds, ctx["version"])
    return f"目录指针切换到 {ctx['version']}"


def _undo_switch_dir(ds, ctx, detail):
    pointer = ds.root / "CURRENT"
    previous = ctx["scratch"].get("previous")
    if previous is None:
        pointer.unlink(missing_ok=True)
    else:
        _write_pointer(ds, previous)


def _act_update_metadata(ds, ctx):
    path = ds.root / "metadata.json"
    ctx["scratch"]["backup"] = path.read_text(encoding="utf-8") if path.exists() else None
    _write_metadata(
        ds,
        current_version=ctx["version"],
        previous_version=ctx["previous"],
        operator=ctx["operator"],
        reason=ctx["reason"],
    )
    return f"元数据更新为 {ctx['version']}"


def _undo_update_metadata(ds, ctx, detail):
    path = ds.root / "metadata.json"
    backup = ctx["scratch"].get("backup")
    if backup is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(backup, encoding="utf-8")


def _act_clear_cache(ds, ctx):
    target = ctx["version"]
    cleared = {k: v for k, v in ds.cache.items() if v["version"] != target}
    for key in cleared:
        del ds.cache[key]
    ctx["scratch"]["cleared"] = cleared
    return f"清理非目标版本缓存 {len(cleared)} 条"


def _undo_clear_cache(ds, ctx, detail):
    ds.cache.update(ctx["scratch"].get("cleared", {}))


def default_pipeline():
    """默认四步流水线：停写、切目录、更新元数据、清缓存。"""
    return [
        Step("stop_writes", _act_stop_writes, _undo_stop_writes),
        Step("switch_dir", _act_switch_dir, _undo_switch_dir),
        Step("update_metadata", _act_update_metadata, _undo_update_metadata),
        Step("clear_cache", _act_clear_cache, _undo_clear_cache),
    ]


def _record_audit(dataset, previous, version, operator, reason, status, step_results):
    _audit.append(
        {
            "timestamp": _now(),
            "operator": operator,
            "dataset": dataset,
            "from": previous,
            "to": version,
            "reason": reason,
            "status": status,
            "steps": [dict(item) for item in step_results],
        }
    )


def switch(dataset, version, operator="system", reason=None, pipeline=None):
    """把 dataset 切换到 version，按序执行流水线，失败自动回退到上一版。

    返回每步结果（step_results）和最终版本（final_version）；
    steps 为兼容字段，固定 ["pointer"]。失败抛 SwitchError 并回退。
    """
    with _lock:
        _validate_name("dataset", dataset)
        _validate_name("version", version)
        ds = _get_or_create(dataset)
        previous = ds.current
        if version == previous:
            raise ValueError(f"数据集 {dataset} 已处于版本 {version}，无需切换")
        if ds.strict and version not in ds.versions:
            raise ValueError(f"数据集 {dataset} 未注册版本 {version}（可用：{sorted(ds.versions)}）")
        if version not in ds.versions:
            _add_version(ds, version)

        steps = list(pipeline) if pipeline is not None else default_pipeline()
        ctx = {
            "version": version,
            "previous": previous,
            "operator": operator,
            "reason": reason,
            "scratch": {},
        }
        step_results = []
        completed = []
        failure = None
        for step in steps:
            ctx["scratch"] = {}
            try:
                detail = step.action(ds, ctx)
            except Exception as exc:
                failure = (step, exc)
                step_results.append({"step": step.name, "status": "failed", "detail": str(exc)})
                break
            step_results.append({"step": step.name, "status": "ok", "detail": detail})
            completed.append((step, detail, ctx["scratch"]))

        if failure is not None:
            rollback_errors = []
            for step, detail, scratch in reversed(completed):
                ctx["scratch"] = scratch
                try:
                    step.compensate(ds, ctx, detail)
                except Exception as exc:
                    rollback_errors.append(f"{step.name}: {exc}")
                    step_results.append(
                        {"step": step.name, "status": "rollback_failed", "detail": str(exc)}
                    )
                else:
                    step_results.append({"step": step.name, "status": "rolled_back", "detail": "已回退"})
            _record_audit(dataset, previous, version, operator, reason, "rolled_back", step_results)
            message = (
                f"切换 {dataset} -> {version} 在步骤 {failure[0].name} 失败：{failure[1]}；"
                f"已回退到 {previous}"
            )
            if rollback_errors:
                message += "；回退未完全成功：" + "；".join(rollback_errors)
            raise SwitchError(
                message, dataset=dataset, version=version, step=failure[0].name, step_results=step_results
            )

        ds.current = version
        _record_audit(dataset, previous, version, operator, reason, "success", step_results)
        return {
            "dataset": dataset,
            "from": previous,
            "to": version,
            "steps": ["pointer"],
            "step_results": step_results,
            "final_version": version,
            "rolled_back": False,
        }
