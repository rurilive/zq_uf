"""按报告拉起互相隔离的漏洞复现环境，并执行验证步骤。

入口：

* :func:`build` —— 记录目标版本、依赖与初始数据，为本次复现准备独立
  workspace；依赖拉取失败停在 ``prepare_failed``，资源超限停在 ``aborted``，
  两者都不进入验证步骤，日志保留失败的包名与版本。
* :func:`verify` —— 按报告顺序执行验证步骤，给出成功/失败与卡住的步骤；
  ``step=`` 给步骤名或下标可单独重跑某一步。
* :func:`cleanup` —— 删除该环境的 workspace，幂等，不影响其他环境。
* :func:`history` / :func:`compare` —— 同一条报告的多次复现放在一起比对。

环境之间目录互不相同、互不影响；记录保存在进程内注册表，workspace 里的
``report.json``/``data/``/``deps/``/``logs/`` 落盘，便于事后排查。
"""

import copy
import hashlib
import json
import os
import shutil
import tempfile
import threading

from ._call import invoke
from .errors import DepResolveError, ReportError, StepError
from .resolver import default_resolver
from .runner import default_runner

_ENVS = {}
_SEQ = [0]
_LOCK = threading.RLock()

_LIMIT_ALIASES = {"disk": "disk_bytes", "steps": "max_steps", "timeout": "timeout_seconds"}


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #


def _default_base_dir():
    root = os.environ.get("REPROENV_HOME")
    if root:
        return os.path.abspath(root)
    return os.path.join(tempfile.gettempdir(), "reproenv")


def _json_safe(value):
    """把 callable 等不可 JSON 序列化的字段换成描述串，落盘不报错。"""
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if callable(value):
        return "<callable %s>" % getattr(value, "__name__", "step")
    return value


def _env_id(env):
    """接受环境记录字典或裸 env_id，返回注册表中存在的 env_id。"""
    if isinstance(env, dict):
        value = env.get("env_id")
    else:
        value = env
    if not value or value not in _ENVS:
        raise ValueError("环境不存在: %r" % (value,))
    return value


def _merge_limits(report, limits):
    merged = {}
    source = report.get("limits") if isinstance(report, dict) else None
    if isinstance(source, dict):
        merged.update(source)
    if isinstance(limits, dict):
        merged.update(limits)
    for short, full in _LIMIT_ALIASES.items():
        if short in merged:
            merged.setdefault(full, merged[short])
            del merged[short]
    return merged


def _load_report(report):
    """报告既可以是字典，也可以是 JSON 文件路径（从报告直接拉起）。"""
    if isinstance(report, (str, os.PathLike)):
        path = os.path.abspath(os.fspath(report))
        try:
            with open(path, encoding="utf-8") as fh:
                report = json.load(fh)
        except OSError as exc:
            raise ReportError("报告文件读取失败: %s (%s)" % (path, exc)) from exc
        except ValueError as exc:
            raise ReportError("报告不是合法 JSON: %s (%s)" % (path, exc)) from exc
    if not isinstance(report, dict):
        raise ReportError("report 必须是字典或 JSON 文件路径")
    return copy.deepcopy(report)


def _append_log(record, message):
    record["logs"].append(message)
    path = os.path.join(record["workspace"], "logs", "build.log")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def _validate_report(report):
    deps = report.get("deps", [])
    steps = report.get("steps", [])
    if not isinstance(deps, list):
        raise ReportError("deps 必须是列表")
    if not isinstance(steps, list):
        raise ReportError("steps 必须是列表")
    for item in steps:
        if not isinstance(item, dict):
            raise ReportError("steps 里的每一项必须是字典，收到 %r" % (item,))
        name = item.get("name", "")
        if name is not None and not isinstance(name, str):
            raise ReportError("步骤名称必须是字符串，收到 %r" % (name,))
    initial = report.get("initial_data", report.get("data"))
    if initial is not None and not isinstance(initial, dict):
        raise ReportError("initial_data 必须是 {文件名: 内容} 的字典")
    return deps, steps


def _safe_relpath(name):
    if not isinstance(name, str) or not name.strip():
        raise ReportError("初始数据文件名必须是非空字符串")
    if os.path.isabs(name) or ".." in name.split("/"):
        raise ReportError("初始数据文件名不合法: %r" % name)
    return name


def _write_initial_data(workspace, initial_data, log):
    """把初始数据写进环境自己的 data/ 目录，返回深拷贝快照。"""
    data_dir = os.path.join(workspace, "data")
    os.makedirs(data_dir, exist_ok=True)
    if initial_data is None:
        return {}
    snapshot = {}
    for name, content in initial_data.items():
        relative = _safe_relpath(name)
        path = os.path.join(data_dir, relative)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if isinstance(content, bytes):
            with open(path, "wb") as fh:
                fh.write(content)
        elif isinstance(content, str):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(content, fh, ensure_ascii=False, sort_keys=True)
        snapshot[relative] = copy.deepcopy(content)
    log.append("初始数据已写入 data/，共 %d 项" % len(snapshot))
    return snapshot


def _report_fingerprint(report):
    """报告内容指纹，便于日志与记录里追溯同一条报告。"""
    payload = json.dumps(_json_safe(report), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _flush_packages(record):
    path = os.path.join(record["workspace"], "deps", "installed.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record["packages"], fh, ensure_ascii=False, sort_keys=True, indent=2)


def _resolve_step_index(record, step):
    if isinstance(step, str):
        for index, item in enumerate(record["steps"]):
            if item.get("name") == step:
                return index
        raise ValueError("步骤不存在: %r" % step)
    if isinstance(step, bool):
        raise ValueError("step 必须是步骤名称或下标")
    if isinstance(step, int):
        if 0 <= step < len(record["steps"]):
            return step
        raise ValueError("步骤下标越界: %r" % step)
    raise ValueError("step 必须是步骤名称或下标")


def _normalize_result(result):
    """把 runner 返回值统一成 ``(ok, actual, detail, duration, abort, reason)``。"""
    if isinstance(result, dict):
        return (
            bool(result.get("ok", True)),
            result.get("actual"),
            result.get("detail", ""),
            result.get("duration"),
            bool(result.get("abort")),
            result.get("reason"),
        )
    if isinstance(result, (tuple, list)):
        values = list(result) + [None] * (4 - len(result))
        ok, actual, detail, duration = values[:4]
        return bool(ok), actual, detail, duration, False, None
    if isinstance(result, bool):
        return result, None, "", None, False, None
    raise ValueError("runner 返回值不合法: %r" % (result,))


# --------------------------------------------------------------------------- #
# 公开入口
# --------------------------------------------------------------------------- #


def build(report, base_dir=None, resolver=None, limits=None):
    """按报告里的版本、依赖和初始数据准备一个隔离环境。

    依赖拉取失败或资源超限时不抛异常，返回 ``state`` 为 ``prepare_failed`` /
    ``aborted`` 的环境记录，此时 :func:`verify` 不会执行任何步骤。
    只有报告本身不合法才抛 :class:`ReportError`（``ValueError`` 子类）。
    """
    report = _load_report(report)
    deps, steps = _validate_report(report)

    resolve_fn = resolver if callable(resolver) else default_resolver
    merged_limits = _merge_limits(report, limits)

    with _LOCK:
        base = os.path.abspath(base_dir) if base_dir else _default_base_dir()
        # 环境标识只在同一 base 下唯一：目录已存在说明撞了（另一个进程
        # 可能刚建过同样的 env-N），换下一个序号，保证环境之间互不影响。
        while True:
            _SEQ[0] += 1
            env_id = "env-%d" % _SEQ[0]
            workspace = os.path.join(base, env_id)
            try:
                # 只认真正新建的目录：残留的旧 workspace 不复用、不覆盖。
                os.mkdir(workspace)
            except FileExistsError:
                continue
            os.makedirs(os.path.join(workspace, "logs"))
            os.makedirs(os.path.join(workspace, "deps"))
            break

    # 环境标识写进报告快照并落盘；调用方传入的原报告不被修改。
    report_snapshot = copy.deepcopy(report)
    report_snapshot["env_id"] = env_id
    report_snapshot["report_fingerprint"] = _report_fingerprint(report)
    report_ref = os.path.join(workspace, "report.json")
    with open(report_ref, "w", encoding="utf-8") as fh:
        json.dump(
            _json_safe(report_snapshot),
            fh,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )

    record = {
        "env_id": env_id,
        "report": report.get("id", ""),
        "report_fingerprint": report_snapshot["report_fingerprint"],
        "report_ref": report_ref,
        "version": report.get("version", ""),
        "deps": list(deps),
        "packages": [],
        "steps": copy.deepcopy(steps),
        "initial_data": {},
        "workspace": workspace,
        "state": "ready",
        "cleanup": "pending",
        "blocked": False,
        "limits": merged_limits,
        "logs": [],
        "results": [],
        "step_results": {},
        "attempts": 0,
        "reason": "",
    }

    try:
        record["initial_data"] = _write_initial_data(
            workspace,
            report.get("initial_data", report.get("data")),
            record["logs"],
        )
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise

    # build 返回的就是注册表里的同一条记录：后续 verify/cleanup 的状态变化
    # 调用方手里的 env 能立刻看到（也便于修正步骤后单独重跑）。
    _ENVS[env_id] = record

    # ---- 准备阶段：逐个拉取依赖，失败即停机，绝不进入验证步骤 ----
    disk_used = 0
    disk_limit = merged_limits.get("disk_bytes")
    for spec in deps:
        try:
            package = invoke(resolve_fn, (spec, report))
            if not isinstance(package, dict) or not package.get("name"):
                raise DepResolveError(
                    str(spec), reason="resolver 返回值不合法", spec=str(spec)
                )
        except DepResolveError as exc:
            record["state"] = "prepare_failed"
            record["blocked"] = True
            record["reason"] = exc.reason
            _append_log(
                record,
                "依赖拉取失败: %s（%s），停在准备阶段"
                % (exc.spec, exc.reason),
            )
            _flush_packages(record)
            return record
        except ReportError:
            # 依赖声明本身不合法：撤掉半成品环境再上抛，避免留垃圾目录。
            _ENVS.pop(env_id, None)
            shutil.rmtree(workspace, ignore_errors=True)
            raise

        package = dict(package)
        package.setdefault("version", "")
        package.setdefault("spec", spec)
        package.setdefault("status", "resolved")
        record["packages"].append(package)
        size = int(package.get("size", 0) or 0)
        disk_used += size
        _append_log(
            record,
            "依赖已就位: %s（%d 字节）" % (package.get("spec"), size),
        )
        if disk_limit is not None and disk_used > disk_limit:
            record["state"] = "aborted"
            record["blocked"] = True
            record["reason"] = "resource-limit:disk_bytes"
            _append_log(
                record,
                "占用资源超预期，提前终止: 依赖累计 %d 字节 > 上限 %d 字节"
                % (disk_used, disk_limit),
            )
            _flush_packages(record)
            return record

    _flush_packages(record)
    _append_log(record, "准备阶段完成，环境就绪")
    return record


def verify(env, step=None, runner=None, limits=None):
    """执行报告里的验证步骤，返回每步结果、整体成败与卡住的步骤。

    ``step`` 给步骤名或下标时只重跑那一步，结果带 ``rerun`` 字段，且不改动
    整环境的聚合状态；不传则完整验证。依赖没准备好的环境直接返回失败，
    不执行任何步骤。``limits`` 与报告里的 limits 合并（调用方覆盖报告）。
    """
    env_id = _env_id(env)
    record = _ENVS[env_id]

    if record["state"] == "removed":
        raise ValueError("环境已清理，无法验证: %s" % env_id)
    record["attempts"] += 1
    if record["blocked"]:
        return _verify_output(record, [], ok=False, reason=record["reason"], rerun=None)

    run_fn = runner if callable(runner) else default_runner
    active_limits = _merge_limits({"limits": record["limits"]}, limits)
    record["limits"] = active_limits
    rerun_index = _resolve_step_index(record, step) if step is not None else None

    max_steps = active_limits.get("max_steps")
    if rerun_index is None and max_steps is not None and len(record["steps"]) > max_steps:
        return _abort(record, "resource-limit:max_steps", "步骤数 %d > 上限 %d" % (len(record["steps"]), max_steps))

    indexes = (
        list(range(len(record["steps"]))) if rerun_index is None else [rerun_index]
    )
    timeout = active_limits.get("timeout_seconds")

    results = []
    elapsed = 0.0
    failed_step = None
    aborted = False
    reason = ""

    for index in indexes:
        item = record["steps"][index]
        name = item.get("name", "")
        try:
            raw = invoke(run_fn, (item, record))
        except StepError as exc:
            raw = {
                "ok": False,
                "actual": exc.actual,
                "detail": exc.detail,
                "abort": exc.abort,
            }
        ok, actual, detail, runner_duration, runner_abort, abort_reason = _normalize_result(raw)
        duration = float(
            runner_duration
            if runner_duration is not None
            else item.get("duration", 0) or 0
        )
        elapsed += duration
        entry = {
            "index": index,
            "step": name,
            "ok": bool(ok),
            "expect": item.get("expect"),
            "actual": actual,
            "detail": detail,
            "duration": duration,
            "aborted": False,
        }

        if runner_abort:
            entry["ok"] = False
            entry["aborted"] = True
            entry["detail"] = detail or "runner 提前终止"
            aborted = True
            reason = abort_reason or "aborted-by-runner"
            results.append(entry)
            failed_step = name
            break

        if timeout is not None and elapsed > timeout:
            entry["ok"] = False
            entry["aborted"] = True
            entry["detail"] = "占用资源超预期，提前终止: 累计 %.3fs > 上限 %.3fs" % (
                elapsed,
                timeout,
            )
            aborted = True
            reason = "resource-limit:timeout_seconds"
            results.append(entry)
            failed_step = name
            break

        results.append(entry)
        if not ok:
            failed_step = name
            break

    overall_ok = failed_step is None and not aborted
    if rerun_index is None:
        record["state"] = (
            "aborted" if aborted else ("verified" if overall_ok else "verify_failed")
        )
        record["reason"] = reason
        record["results"] = copy.deepcopy(results)
        record["step_results"] = {entry["index"]: entry for entry in results}
    else:
        # 单步重跑：只更新该步缓存，不动整环境的聚合状态。
        record["step_results"][rerun_index] = copy.deepcopy(results[-1])
    output = _verify_output(
        record,
        results,
        ok=overall_ok,
        reason=reason,
        rerun=(
            record["steps"][rerun_index].get("name", "")
            if rerun_index is not None
            else None
        ),
        failed_step=failed_step,
    )
    _write_verify_log(record, results, output)
    return output


def _abort(record, reason, detail):
    record["state"] = "aborted"
    record["reason"] = reason
    record["results"] = []
    _append_log(record, "占用资源超预期，提前终止: %s" % detail)
    return _verify_output(record, [], ok=False, reason=reason, rerun=None)


def _verify_output(record, results, ok, reason, rerun, failed_step=None):
    """组装 verify 返回结构；不含尝试序号等易变字段，保证重复验证结果一致。"""
    out = {
        "env_id": record["env_id"],
        "report": record["report"],
        "version": record["version"],
        "results": copy.deepcopy(results),
        "ok": bool(ok),
        "failed_step": failed_step,
        "status": record["state"],
        "cleanup": record["cleanup"],
        "reason": reason,
    }
    if rerun is not None:
        out["rerun"] = rerun
    return out


def _write_verify_log(record, results, output):
    log_dir = os.path.join(record["workspace"], "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "verify-%d.log" % record["attempts"])
    with open(path, "w", encoding="utf-8") as fh:
        for entry in results:
            line = "[%s] %s: %s" % (
                "OK" if entry["ok"] else "FAIL",
                entry["step"],
                entry["detail"] or ("actual=%r" % (entry["actual"],)),
            )
            fh.write(line + "\n")
        fh.write(
            "整体结果: ok=%s failed_step=%s\n" % (output["ok"], output["failed_step"])
        )


def cleanup(env):
    """删除环境 workspace 并登记清理状态；幂等，不影响其他环境。"""
    env_id = _env_id(env)
    record = _ENVS[env_id]
    workspace = record["workspace"]
    if record["state"] != "removed":
        shutil.rmtree(workspace, ignore_errors=True)
        if os.path.exists(workspace):
            # 删除失败保留原状态，下次 cleanup 仍会重试。
            record["cleanup"] = "failed"
        else:
            record["state"] = "removed"
            record["cleanup"] = "removed"
    return {
        "env_id": env_id,
        "removed": record["cleanup"] == "removed",
        "cleanup": record["cleanup"],
        "workspace": workspace,
    }


def history(report=None):
    """列出复现记录；``report`` 给报告 id 时只看同一条报告的多次复现。"""
    report_id = report.get("id", "") if isinstance(report, dict) else report
    rows = []
    for record in _ENVS.values():
        if report_id is not None and record["report"] != report_id:
            continue
        rows.append(
            {
                "env_id": record["env_id"],
                "report": record["report"],
                "version": record["version"],
                "state": record["state"],
                "cleanup": record["cleanup"],
                "ok": _all_ok(record["results"]) if record["results"] else None,
                "runs": record["attempts"],            }
        )
    return rows


def _all_ok(results):
    return bool(results) and all(entry["ok"] for entry in results)


def _canonical(record):
    """抽掉 env_id/workspace 等易变字段，只留可比对的复现结论。"""
    if record["blocked"]:
        return {"ok": False, "reason": record["reason"], "results": []}
    if not record["results"]:
        # 还没验证过，没有可比的结论。
        return None
    return {
        "ok": _all_ok(record["results"]),
        "reason": record["reason"],
        "results": [
            (entry["step"], entry["ok"], _jsonable(entry.get("actual")))
            for entry in record["results"]
        ],
    }


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def compare(*envs):
    """把同一条报告的多个复现结果放在一起比对，判断结论是否一致。"""
    if not envs:
        raise ValueError("compare 至少需要一个环境")
    if len(envs) == 1 and isinstance(envs[0], (list, tuple)):
        envs = tuple(envs[0])
    records = [_ENVS[_env_id(item)] for item in envs]
    report_id = records[0]["report"]
    if any(record["report"] != report_id for record in records):
        raise ValueError("只能比对同一条报告的复现结果")
    for record in records:
        if _canonical(record) is None:
            raise ValueError("环境还没验证过，无法比对: %s" % record["env_id"])

    runs = []
    for record in records:
        runs.append(
            {
                "env_id": record["env_id"],
                "version": record["version"],
                "canonical": _canonical(record),
            }
        )
    canonicals = [run["canonical"] for run in runs]
    consistent = all(item == canonicals[0] for item in canonicals)
    diffs = []
    if not consistent:
        for run in runs[1:]:
            if run["canonical"] != canonicals[0]:
                diffs.append("%s 与 %s 结论不一致" % (run["env_id"], runs[0]["env_id"]))
    return {
        "report": report_id,
        "consistent": consistent,
        "runs": runs,
        "diffs": diffs,
    }


def reset(remove_dirs=True):
    """清空进程内注册表；测试隔离用，默认同时删掉登记过的环境 workspace。

    只删各环境自己的 workspace（含空的 env 目录），不动调用方传入的
    ``base_dir`` 本身，避免误删用户目录。
    """
    with _LOCK:
        records = list(_ENVS.values())
        _ENVS.clear()
        _SEQ[0] = 0
        if remove_dirs:
            for record in records:
                shutil.rmtree(record["workspace"], ignore_errors=True)
