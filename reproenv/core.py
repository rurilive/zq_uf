"""按报告拉起相互隔离的漏洞复现环境。

入口：
- build(report)：按报告里的目标版本、依赖和初始数据准备独立环境，
  依赖拉取失败时停在准备阶段（state="prepare_failed"），不进入验证。
- verify(env)：在环境内逐个执行报告里的验证步骤，返回每步结果、
  整体是否成功以及卡在哪一步；支持 only= 单独重跑指定步骤。
- cleanup(env)：删除该环境的工作目录，环境之间互不影响。
- compare(*outputs)：把同一条报告的多次复现结果放在一起比对。

默认安装器与步骤执行器是确定性的本地模拟器（不发起网络、不起子进程），
故障通过报告里的 fail_deps / fail_steps 或注入的 installer / runner 触发，
因此同一条报告重复验证的结果逐字段一致。
"""

import copy
import json
import os
import shutil
import tempfile
import time

# 资源占用上限，报告的 resource_limit 字段或 verify 的 limits 参数可覆盖。
DEFAULT_LIMITS = {
    "max_steps": 50,               # 单次验证允许执行的步骤数
    "max_workspace_bytes": 50 << 20,  # 工作目录体积上限，默认 50MiB
    "step_timeout_seconds": 30,    # 单步执行耗时上限
}

# 环境生命周期状态。
STATE_READY = "ready"
STATE_PREPARE_FAILED = "prepare_failed"
STATE_VERIFIED = "verified"
STATE_VERIFY_FAILED = "verify_failed"
STATE_ABORTED = "aborted"
STATE_CLEANED = "cleaned"

_ENVS = {}          # env_id -> 环境记录
_SEQ = [0]
_CONFIG = {
    "root": None,                # 工作区根目录，首次使用时惰性创建
    "installer": None,           # 全局依赖安装器注入
    "step_runner": None,         # 全局步骤执行器注入
}

_PUBLIC_KEYS = (
    "env_id", "report_id", "version", "deps", "steps", "data",
    "workspace", "state", "cleanup", "prepare_error", "limits",
)


# ---------------------------------------------------------------- 工具函数

def reset():
    """清空环境登记表（主要给测试用，不删除已落盘的工作目录）。"""
    _ENVS.clear()
    _SEQ[0] = 0


def configure(root=None, installer=None, step_runner=None):
    """注入工作区根目录或自定义安装器/步骤执行器，None 表示不改该项。"""
    if root is not None:
        _CONFIG["root"] = root
    if installer is not None:
        _CONFIG["installer"] = installer
    if step_runner is not None:
        _CONFIG["step_runner"] = step_runner


def _root_dir():
    if _CONFIG["root"] is None:
        _CONFIG["root"] = tempfile.mkdtemp(prefix="reproenv-")
    return _CONFIG["root"]


def _public_view(record):
    """对外返回的环境副本，隐藏内部字段。"""
    return {key: copy.deepcopy(record[key]) for key in _PUBLIC_KEYS}


def _resolve(env):
    """env 可以是环境记录字典，也可以直接给 env_id。"""
    env_id = env.get("env_id") if isinstance(env, dict) else env
    record = _ENVS.get(env_id)
    if record is None:
        raise ValueError("环境不存在: %r" % env_id)
    return env_id, record


def _parse_dep(raw):
    """把依赖规格统一成 {name, version, spec}。"""
    if isinstance(raw, str) and raw.strip():
        spec = raw.strip()
        if "==" in spec:
            name, version = spec.split("==", 1)
        else:
            name, version = spec, ""
        return {"name": name.strip(), "version": version.strip(), "spec": spec}
    if isinstance(raw, dict) and raw.get("name"):
        name = str(raw["name"]).strip()
        version = str(raw.get("version", "")).strip()
        spec = "%s==%s" % (name, version) if version else name
        return {"name": name, "version": version, "spec": spec}
    raise ValueError("无法识别的依赖规格: %r" % (raw,))


def _fail_set(values):
    """归一化故障注入列表，同时保留 name==version 和裸 name 两种写法。"""
    out = set()
    for value in values or []:
        dep = _parse_dep(value)
        out.add(dep["spec"])
        out.add(dep["name"])
    return out


def _log(record, log_name, message):
    """追加一行带时间戳的日志，失败信息也会落盘保留。"""
    path = os.path.join(record["workspace"], "logs", log_name)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), message))


def _write_manifest(record):
    """环境标识写进报告并随 manifest 落盘，供后续比对追溯。"""
    manifest = {
        "env_id": record["env_id"],
        "report": record["_report"],
        "version": record["version"],
        "deps": record["deps"],
        "state": record["state"],
    }
    with open(os.path.join(record["workspace"], "manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)


def _workspace_size(path):
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(dirpath, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- 默认模拟器

def _default_installer(record, dep):
    """本地模拟安装：命中 fail_deps 即按“拉取失败”处理。"""
    if dep["spec"] in record["_fail_deps"] or dep["name"] in record["_fail_deps"]:
        raise RuntimeError("依赖拉取失败: %s（包名=%s 版本=%s）"
                           % (dep["spec"], dep["name"], dep["version"] or "-"))
    return True


def _default_runner(record, step):
    """本地模拟执行：默认回显 expect；命中 fail_steps 或 step.fail 即失败。"""
    name = step.get("name", "")
    if step.get("fail") or name in record["_fail_steps"]:
        return step.get("actual_on_fail", "")
    return step.get("actual", step.get("expect", ""))


# ---------------------------------------------------------------- build

def build(report, installer=None):
    """按报告准备一个隔离的复现环境，返回环境记录（含环境标识和目标版本）。

    依赖拉取失败时环境停在 prepare_failed：工作目录与日志保留，
    但 verify 会拒绝执行任何验证步骤。
    """
    if not isinstance(report, dict):
        raise ValueError("report 必须是字典")
    deps_raw = report.get("deps", [])
    steps = report.get("steps", [])
    if not isinstance(deps_raw, list) or not isinstance(steps, list):
        raise ValueError("deps 和 steps 必须是列表")
    if any(not isinstance(step, dict) for step in steps):
        raise ValueError("steps 里的每一项必须是字典")

    deps = [_parse_dep(item) for item in deps_raw]
    limits = dict(DEFAULT_LIMITS)
    if isinstance(report.get("resource_limit"), dict):
        limits.update(report["resource_limit"])

    _SEQ[0] += 1
    env_id = "env-%d" % _SEQ[0]
    workspace = os.path.join(_root_dir(), env_id)
    os.makedirs(os.path.join(workspace, "logs"))
    os.makedirs(os.path.join(workspace, "results"))

    report_copy = copy.deepcopy(report)
    report_copy["env_id"] = env_id  # 环境标识写进报告副本

    record = {
        "env_id": env_id,
        "report_id": report.get("id", ""),
        "version": report.get("version", ""),
        "deps": [d["spec"] for d in deps],
        "steps": copy.deepcopy(steps),
        "data": copy.deepcopy(report.get("data")),
        "workspace": workspace,
        "state": STATE_READY,
        "cleanup": "pending",
        "prepare_error": None,
        "limits": limits,
        "_report": report_copy,
        "_fail_deps": _fail_set(report.get("fail_deps", [])),
        "_fail_steps": set(report.get("fail_steps", []) or []),
        "_results": {},
        "_last_output": None,
    }
    _ENVS[env_id] = record

    # 初始数据落盘，保证每次复现起点一致。
    if record["data"] is not None:
        with open(os.path.join(workspace, "data.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(record["data"], fh, ensure_ascii=False, indent=2)
    _write_manifest(record)

    do_install = installer or _CONFIG["installer"] or _default_installer
    # ---- 准备阶段：逐个拉取依赖，失败即停，不进入验证 ----
    for dep in deps:
        try:
            do_install(record, dep)
        except Exception as exc:  # 任何安装器异常都视为依赖拉取失败
            record["state"] = STATE_PREPARE_FAILED
            record["prepare_error"] = {
                "dep": dep["spec"],
                "name": dep["name"],
                "version": dep["version"],
                "error": str(exc),
            }
            _log(record, "prepare.log",
                 "FAIL %s name=%s version=%s error=%s"
                 % (dep["spec"], dep["name"], dep["version"] or "-", exc))
            _write_manifest(record)
            return _public_view(record)
        _log(record, "prepare.log", "OK   %s" % dep["spec"])

    _log(record, "prepare.log", "prepare finished, state=ready")
    _write_manifest(record)
    return _public_view(record)


# ---------------------------------------------------------------- verify

def _abort(record, reason, aborted_at, results_so_far):
    """资源超预期时提前终止。"""
    record["state"] = STATE_ABORTED
    output = {
        "env_id": record["env_id"],
        "report_id": record["report_id"],
        "version": record["version"],
        "results": [results_so_far[i] for i in sorted(results_so_far)],
        "ok": False,
        "failed_step": None,
        "reason": reason,
        "aborted_at": aborted_at,
        "cleanup": record["cleanup"],
    }
    record["_last_output"] = output
    _log(record, "verify.log", "ABORT reason=%s at=%s" % (reason, aborted_at))
    _write_manifest(record)
    return output


def verify(env, only=None, runner=None, limits=None):
    """执行报告里的验证步骤。

    only 传步骤名或步骤名列表时只跑指定步骤（单独重跑），其余步骤沿用
    上一次的结果。返回每步结果、整体 ok、卡在哪一步以及清理状态。
    """
    _env_id, record = _resolve(env)
    if record["state"] == STATE_PREPARE_FAILED:
        raise RuntimeError(
            "环境 %s 准备阶段失败，未进入验证；失败依赖: %s"
            % (record["env_id"], record["prepare_error"]["dep"]))
    if record["state"] == STATE_CLEANED:
        raise RuntimeError("环境 %s 已清理，无法再验证" % record["env_id"])
    if not os.path.isdir(record["workspace"]):
        raise RuntimeError("工作目录已不存在: %s" % record["workspace"])

    effective_limits = dict(record["limits"])
    if limits:
        effective_limits.update(limits)

    steps = record["steps"]
    if only is None:
        indexes = list(range(len(steps)))
        record["_results"] = {}  # 全量重跑，历史结果作废
    else:
        wanted = {only} if isinstance(only, str) else set(only)
        indexes = [i for i, step in enumerate(steps)
                   if step.get("name", "") in wanted]
        unknown = wanted - {step.get("name", "") for step in steps}
        if unknown:
            raise ValueError("步骤不存在: %s" % ", ".join(sorted(unknown)))

    if len(indexes) > effective_limits["max_steps"]:
        return _abort(record, "resource_limit:max_steps",
                      steps[indexes[0]].get("name", ""), record["_results"])

    do_run = runner or _CONFIG["step_runner"] or _default_runner
    results = record["_results"]

    for index in indexes:
        step = steps[index]
        name = step.get("name", "")
        expect = step.get("expect", "")

        # 每步执行前检查工作目录占用，超预期就提前终止。
        size = _workspace_size(record["workspace"])
        if size > effective_limits["max_workspace_bytes"]:
            return _abort(record, "resource_limit:max_workspace_bytes",
                          name, results)

        started = time.monotonic()
        try:
            actual = do_run(record, step)
        except Exception as exc:
            actual = ""
            runner_error = str(exc)
        else:
            runner_error = None
        elapsed = time.monotonic() - started

        if elapsed > effective_limits["step_timeout_seconds"]:
            return _abort(record, "resource_limit:step_timeout_seconds",
                          name, results)

        ok = runner_error is None and actual == expect
        result = {
            "step": name,
            "expect": expect,
            "actual": actual,
            "ok": ok,
            "elapsed_seconds": round(elapsed, 6),
        }
        if runner_error is not None:
            result["error"] = runner_error
        results[index] = result

        result_path = os.path.join(
            record["workspace"], "results", "%d-%s.json" % (index, name or "step"))
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        _log(record, "verify.log",
             "%s step=%s ok=%s" % ("OK  " if ok else "FAIL", name, ok))

        if not ok:  # 失败即停，failed_step 标明卡在哪一步
            record["state"] = STATE_VERIFY_FAILED
            output = {
                "env_id": record["env_id"],
                "report_id": record["report_id"],
                "version": record["version"],
                "results": [results[i] for i in sorted(results)],
                "ok": False,
                "failed_step": name,
                "reason": "step_failed",
                "aborted_at": None,
                "cleanup": record["cleanup"],
            }
            record["_last_output"] = output
            _write_manifest(record)
            return output

    record["state"] = STATE_VERIFIED
    output = {
        "env_id": record["env_id"],
        "report_id": record["report_id"],
        "version": record["version"],
        "results": [results[i] for i in sorted(results)],
        "ok": True,
        "failed_step": None,
        "reason": None,
        "aborted_at": None,
        "cleanup": record["cleanup"],
    }
    record["_last_output"] = output
    _write_manifest(record)
    return output


def rerun_step(env, step_name, runner=None, limits=None):
    """单独重跑某一个验证步骤，返回该步骤的最新结果。"""
    output = verify(env, only=step_name, runner=runner, limits=limits)
    for result in output["results"]:
        if result["step"] == step_name:
            return result
    raise ValueError("步骤不存在: %s" % step_name)


# ---------------------------------------------------------------- cleanup

def cleanup(env):
    """删除环境工作目录，返回清理状态；重复调用保持幂等。"""
    _env_id, record = _resolve(env)
    if record["state"] == STATE_CLEANED or not os.path.exists(
            record["workspace"]):
        record["state"] = STATE_CLEANED
        record["cleanup"] = "removed"
        return {
            "env_id": record["env_id"],
            "removed": True,
            "workspace": record["workspace"],
            "cleanup": "removed",
        }
    try:
        shutil.rmtree(record["workspace"])
    except OSError as exc:
        record["cleanup"] = "failed"
        return {
            "env_id": record["env_id"],
            "removed": False,
            "workspace": record["workspace"],
            "cleanup": "failed",
            "error": str(exc),
        }
    record["state"] = STATE_CLEANED
    record["cleanup"] = "removed"
    return {
        "env_id": record["env_id"],
        "removed": True,
        "workspace": record["workspace"],
        "cleanup": "removed",
    }


# ---------------------------------------------------------------- 比对

def compare(*outputs):
    """把同一条报告的多次 verify 结果放在一起比对。

    所有复现的步骤顺序、每步 ok 与 actual 完全一致时 consistent=True。
    """
    if not outputs:
        raise ValueError("compare 至少需要一个验证结果")
    step_names = [[r["step"] for r in out["results"]] for out in outputs]
    same_steps = all(names == step_names[0] for names in step_names)
    consistent = same_steps and all(
        all(a.get("ok") == b.get("ok") and a.get("actual") == b.get("actual")
            for a, b in zip(out["results"], outputs[0]["results"]))
        for out in outputs[1:]
    ) if same_steps else False
    rows = []
    for i, row in enumerate(outputs[0]["results"]):
        rows.append({
            "step": row["step"],
            "ok_all": same_steps and all(
                out["results"][i].get("ok") for out in outputs),
            "actuals": [out["results"][i].get("actual") for out in outputs]
            if same_steps else [],
        })
    return {
        "report_id": outputs[0].get("report_id", ""),
        "env_ids": [out["env_id"] for out in outputs],
        "consistent": consistent,
        "steps": rows,
    }


def find_envs(report_id=None):
    """按报告 id 列出环境副本（不传 id 则列出全部），便于多轮复现追溯。"""
    return [
        _public_view(record)
        for record in _ENVS.values()
        if report_id is None or record["report_id"] == report_id
    ]
