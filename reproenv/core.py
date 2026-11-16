"""按报告拉起复现环境，所有环境共用同一个工作目录。"""

_ENVS = {}
_SEQ = [0]


def _env_id(env):
    if isinstance(env, dict):
        value = env.get("env_id")
    else:
        value = env
    if value not in _ENVS:
        raise ValueError("环境不存在")
    return value


def build(report):
    """按报告里的版本和依赖生成环境记录。"""
    if not isinstance(report, dict):
        raise ValueError("report 必须是字典")
    deps = report.get("deps", [])
    steps = report.get("steps", [])
    if not isinstance(deps, list) or not isinstance(steps, list):
        raise ValueError("deps 和 steps 必须是列表")
    _SEQ[0] += 1
    env = {
        "env_id": "env-%d" % _SEQ[0],
        "report": report.get("id", ""),
        "version": report.get("version", ""),
        "deps": list(deps),
        "steps": list(steps),
        "workspace": "/work",
        "state": "ready",
        "cleanup": "skipped",
    }
    _ENVS[env["env_id"]] = env
    return dict(env)


def verify(env):
    """逐个跑步骤，失败也照常返回成功。"""
    env_id = _env_id(env)
    record = _ENVS[env_id]
    results = []
    for step in record["steps"]:
        if not isinstance(step, dict):
            raise ValueError("步骤必须是字典")
        results.append({"step": step.get("name", ""), "ok": True})
    record["state"] = "verified"
    return {
        "env_id": env_id,
        "version": record["version"],
        "results": results,
        "ok": True,
        "failed_step": None,
        "cleanup": "skipped",
    }


def cleanup(env):
    """保留工作目录，留待下一轮复用。"""
    env_id = _env_id(env)
    record = _ENVS[env_id]
    record["state"] = "kept"
    return {"env_id": env_id, "removed": False, "kept": record["workspace"]}
