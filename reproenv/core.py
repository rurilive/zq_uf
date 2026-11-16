"""按报告拉起互相隔离的漏洞复现环境，并执行验证步骤。

入口：

* :func:`build` —— 记录目标版本、依赖与初始数据，准备独立 workspace；
  依赖拉取失败停在准备阶段，资源超限提前终止，均不抛异常而是返回带状态的环境记录。
* :func:`verify` —— 按报告顺序执行验证步骤，给出成功/失败与卡住的步骤，
  支持用 ``step=`` 单独重跑某一步。
* :func:`cleanup` —— 删除该环境的 workspace，登记清理状态。
* :func:`history` / :func:`compare` —— 同一条报告的多次复现放在一起比对。

环境之间目录互不相同、互不影响；所有记录只保存在进程内注册表，
workspace 里的 report.json/data/deps/logs 落盘，便于事后排查。
"""

import copy
import json
import os
import shutil
import tempfile
import threading

from .errors import DepResolveError, ReportError, StepError
from .resolver import default_resolver
from .runner import default_runner

_ENVS = {}
_SEQ = [0]
_LOCK = threading.RLock()


def _default_base_dir():
    root = os.environ.get("REPROENV_HOME")
    if root:
        return root
    return os.path.join(tempfile.gettempdir(), "reproenv")


def _json_safe(value):
    """把 callable 等不可 JSON 序列化的步骤字段换成描述串，落盘不报错。"""
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
    source = report.get("limits")
    if isinstance(source, dict):
        merged.update(source)
    if isinstance(limits, dict):
        merged.update(limits)
    aliases = {"disk": "disk_bytes", "steps": "max_steps", "timeout": "timeout_seconds"}
    for short, full in aliases.items():
        if short in merged and full not in merged:
            merged[full] = merged.pop(short)
        merged.pop(short, None)
    return merged


def _write_initial_data(workspace, initial_data, log):
    """把初始数据写进环境自己的 data/ 目录，返回深拷贝副本。"""
    data_dir = os.path.join(workspace, "data")
    os.makedirs(data_dir, exist_ok=True)
    if initial_data is None:
        return {}
    if not isinstance(initial_data, dict):
        raise ReportError("initial_data 必须是 {文件名: 内容} 的字典")
    snapshot = {}
    for name, content in initial_data.items():
        if not isinstance(name, str) or not name:
            raise ReportError("初始数据文件名必须是非空字符串")
        if os.path.isabs(name) or ".." in name.split("/"):
            raise ReportError("初始数据文件名不合法: %r" % name)
        path = os.path.join(data_dir, name)
        os.makedirs(os.path.dirname(path) or data_dir, exist_ok=True)
        if isinstance(content, bytes):
            with open(path, "wb") as fh:
                fh.write(content)
        elif isinstance(content, str):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(content, fh, ensure_ascii=False, sort_keys=True)
        snapshot[name] = content
    log.append("初始数据已写入 data/，共 %d 项" % len(snapshot))
    return copy.deepcopy(snapshot)


def _append_log(record, message):
    record["logs"].append(message)
    log_path = os.path.join(record["workspace"], "logs", "build.log")
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def build(report, base_dir=None, resolver=None, limits=None):
    """按报告里的版本和依赖准备一个隔离环境。

    依赖拉取失败或资源超限时不抛异常，返回 ``state`` 为
    ``prepare_failed`` / ``aborted`` 的环境记录，verify 不会执行任何步骤。
    报告本身格式错误才抛 :class:`ReportError`（ValueError 子类）。
    """
    if not isinstance(report, dict):
        raise ReportError("report 必须是字典")
    deps = report.get("deps", [])
    steps = report.get("steps", [])
    if not isinstance(deps, list) or not isinstance(steps, list):
        raise ReportError("deps 和 steps 必须是列表")
    if any(not isinstance(step, dict) for step in steps):
        raise ReportError("steps 里的每一项必须是字典")

    resolve_fn = resolver if callable(resolver) else default_resolver
    report_copy = copy.deepcopy(report)
    merged_limits = _merge_limits(report, limits)

    with _LOCK:
        _SEQ[0] += 1
        env_id = "env-%d" % _SEQ[0]

    base = base_dir or _default_base_dir()
    workspace = os.path.join(os.path.abspath(base), env_id)
    os.makedirs(os.path.join(workspace, "logs"))
    os.makedirs(os.path.join(workspace, "deps"))

    # 环境标识写进报告快照并落盘，报告原件不被修改。
    report_snapshot = dict(report_copy)
    report_snapshot["env_id"] = env_id
    with open(os.path.join(workspace, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(_json_safe(report_snapshot), fh, ensure_ascii=False, sort_keys=True, indent=2)

    record = {
        "env_id": env_id,
        "report": report.get("id", ""),
        "report_ref": os.path.join(workspace, "report.json"),
        "version": report.get("version", ""),
        "deps": list(deps),
        "packages": [],
        "steps": copy.deepcopy(steps),
        "initial_data": {},
        "workspace": workspace,
        "state": "ready",
        "cleanup": "pending",
        "limits": merged_limits,
        "logs": [],
        "attempts": 0,
        "step_results": {},
        "verifications": [],
        "reason": "",
    }

    try:
        record["initial_data"] = _write_initial_data(
            workspace, report.get("initial_data", report.get("data")), record["logs"]
        )
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise

    # 提前登记：build 返回的字典与注册表是同一对象，后续 verify/cleanup
    # 的状态变化调用方手里的 env 能立刻看到。
    _ENVS[env_id] = record

    # ---- 准备阶段：逐个拉取依赖，失败即停机，绝不进入验证步骤 ----
    disk_used = 0
    disk_limit = merged_limits.get("disk_bytes")
    for spec in deps:
        try:
            package = resolve_fn(spec, report_copy)
            if not isinstance(package, dict) or "name" not in package:
                raise DepResolveError(str(spec), reason="resolver 返回值不合法", spec=spec)
        except DepResolveError as exc:
            record["state"] = "prepare_failed"
            record["reason"] = exc.reason
            failed = "依赖拉取失败: %s（%s）" % (exc.spec, exc.reason)
            _append_log(record, failed)
            return record

        record["packages"].append(package)
        disk_used += int(package.get("size", 0))
        _append_log(
            record,
            "依赖已就位: %s（%d 字节）"
            % (package.get("spec") or package["name"], package.get("size", 0)),
        )
        if disk_limit is not None and disk_used > disk_limit:
            record["state"] = "aborted"
            record["reason"] = "resource-limit:disk_bytes"
            _append_log(
                record,
                "占用资源超预期，提前终止: 累计 %d 字节 > 上限 %d 字节"
                % (disk_used, disk_limit),
            )
            _flush_packages(record)
            return record

    _flush_packages(record)
    _append_log(record, "准备阶段完成，环境就绪")
    return record


def _flush_packages(record):
    path = os.path.join(record["workspace"], "deps", "installed.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record["packages"], fh, ensure_ascii=False, sort_keys=True, indent=2)


def _resolve_step_index(record, step):
    """step 支持名称或下标，返回合法下标。"""
    if isinstance(step, str):
        for index, item in enumerate(record["steps"]):
            if item.get("name") == step:
                return index
        raise ValueError("步骤不存在: %r" % step)
    if isinstance(step, int) and not isinstance(step, bool):
        if 0 <= step < len(record["steps"]):
            return step
        raise ValueError("步骤下标越界: %r" % step)
    raise ValueError("step 必须是步骤名称或下标")


def verify(env, step=None, runner=None, limits=None):
    """执行报告里的验证步骤，返回每步结果与整体成败。

    ``step`` 给步骤名或下标时只重跑那一步（单独重跑），结果里带 ``rerun``。
    依赖没准备好的环境直接返回失败，不会执行任何步骤。
    ``limits`` 与报告里的 limits 合并（调用方覆盖报告），用于资源护栏。
    """
    env_id = _env_id(env)
    record = _ENVS[env_id]

    if record["state"] == "removed":
        raise ValueError("环境已清理，无法验证: %s" % env_id)
    if record["state"] in ("prepare_failed", "aborted"):
        return _verify_output(record, [], ok=False, reason=record["reason"], rerun=None)

    run_fn = runner if callable(runner) else default_runner
    active_limits = _merge_limits({"limits": record["limits"]}, limits)
    record["limits"] = active_limits
    rerun_index = _resolve_step_index(record, step) if step is not None else None

    max_steps = active_limits.get("max_steps")
    if rerun_index is None and max_steps is not None and len(record["steps"]) > max_steps:
        record["state"] = "aborted"
        record["reason"] = "resource-limit:max_steps"
        record["logs"].append(
            "占用资源超预期，提前终止: 步骤数 %d > 上限 %d"
            % (len(record["steps"]), max_steps)
        )
        return _verify_output(record, [], ok=False, reason=record["reason"], rerun=None)

    if rerun_index is None:
        indexes = list(range(len(record["steps"])))
    else:
        indexes = [rerun_index]

    results = []
    elapsed = 0.0
    timeout = record["limits"].get("timeout_seconds")
    failed_step = None
    aborted = False
    reason = ""

    for index in indexes:
        item = record["steps"][index]
        name = item.get("name", "")
        try:
            result = run_fn(item, record)
        except StepError as exc:
            result = (False, exc.actual, exc.detail, None)
        # 自定义 runner 可返回 (ok, actual, detail, duration) 元组，
        # 也可返回带 ok/actual/detail/duration/abort 的结果字典。
        if isinstance(result, dict):
            ok = result.get("ok", True)
            actual = result.get("actual")
            detail = result.get("detail", "")
            runner_duration = result.get("duration")
            runner_abort = bool(result.get("abort"))
        else:
            ok, actual, detail, runner_duration = result
            runner_abort = False
        # runner 实际返回的耗时优先；默认 runner 用步骤声明的 duration 模拟。
        duration = float(runner_duration if runner_duration is not None else item.get("duration", 0) or 0)
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
            reason = result.get("reason", "aborted-by-runner") if isinstance(result, dict) else "aborted-by-runner"
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
        record["state"] = "aborted" if aborted else ("verified" if overall_ok else "verify_failed")
        record["reason"] = reason
        record["step_results"] = {entry["index"]: entry for entry in results}
    else:
        # 单步重跑：只更新该步缓存，不动整环境的聚合状态。
        record["step_results"][rerun_index] = results[-1]

    output = _verify_output(
        record,
        results,
        ok=overall_ok,
        reason=reason,
        rerun=record["steps"][rerun_index].get("name", "") if rerun_index is not None else None,
        failed_step=failed_step,
    )
    record["verifications"].append(copy.deepcopy(output))
    _write_verify_log(record, results, output)
    return output


def _verify_output(record, results, ok, reason, rerun, failed_step=None):
    record["attempts"] += 1
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
        "attempt": record["attempts"],
    }
    if rerun is not None:
        out["rerun"] = rerun
    return out


def _write_verify_log(record, results, output):
    log_dir = os.path.join(record["workspace"], "logs")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "verify-%d.log" % output["attempt"])
    with open(path, "w", encoding="utf-8") as fh:
        for entry in results:
            line = "[%s] %s: %s" % (
                "OK" if entry["ok"] else "FAIL",
                entry["step"],
                entry["detail"] or ("actual=%r" % (entry["actual"],)),
            )
            fh.write(line + "\n")
        fh.write("整体结果: ok=%s failed_step=%s\n" % (output["ok"], output["failed_step"]))


def cleanup(env):
    """删除环境 workspace 并登记清理状态，幂等。"""
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
    """列出复现记录；给报告 id 时只看同一条报告的多次复现。"""
    items = []
    for record in _ENVS.values():
        if report is not None and record["report"] != report:
            continue
        items.append(
            {
                "env_id": record["env_id"],
                "report": record["report"],
                "version": record["version"],
                "state": record["state"],
                "cleanup": record["cleanup"],
                "ok": record["verifications"][-1]["ok"] if record["verifications"] else None,
                "runs": len(record["verifications"]),
            }
        )
    return items


def _canonical(record):
    """抽掉 env_id/attempt 等易变字段，只留可比对的复现结论。"""
    if not record["verifications"]:
        return None
    last = record["verifications"][-1]
    return {
        "ok": last["ok"],
        "failed_step": last["failed_step"],
        "results": [
            (entry["step"], entry["ok"], _jsonable(entry.get("actual")))
            for entry in last["results"]
        ],
    }


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def compare(*envs):
    """把同一条报告的多个环境复现结果放在一起比对。

    入参可以是环境记录字典或 env_id；返回一致性结论与每份复现的规范结果。
    """
    if not envs:
        raise ValueError("compare 至少需要一个环境")
    records = [_ENVS[_env_id(item)] for item in envs]
    report_id = records[0]["report"]
    if any(record["report"] != report_id for record in records):
        raise ValueError("只能比对同一条报告的复现结果")

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
                diffs.append(
                    "%s 与 %s 结论不一致" % (run["env_id"], runs[0]["env_id"])
                )
    return {
        "report": report_id,
        "consistent": consistent,
        "runs": runs,
        "diffs": diffs,
    }


def reset(remove_dirs=True):
    """清空进程内注册表；测试隔离用，默认同时删掉所有 workspace。"""
    with _LOCK:
        if remove_dirs:
            bases = {os.path.dirname(record["workspace"]) for record in _ENVS.values()}
            for base in bases:
                shutil.rmtree(base, ignore_errors=True)
        _ENVS.clear()
        _SEQ[0] = 0
