"""采集流程编排：阶段声明依赖与产物，按入度拓扑执行。

- 阶段处理函数通过 register_handler 注册，流程定义只声明名称、依赖、
  所需上下文字段（requires）和产物（produces），新增阶段不改本模块；
- 失败只重跑失败阶段及其下游，重跑前仅清理受影响阶段的产物；
- 上下文显式传递、可 JSON 序列化，并随恢复令牌一起落盘；
- 恢复令牌带有效期，未知或过期令牌拒绝恢复。
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid

__all__ = [
    "DEFAULT_TOKEN_TTL",
    "FlowError",
    "get_artifact",
    "list_artifacts",
    "load_flow",
    "register_handler",
    "resume",
    "run",
    "set_base_dir",
]

DEFAULT_TOKEN_TTL = 3600.0

_DEFAULT_STAGES = (
    {"name": "fetch", "depends_on": [], "requires": [], "produces": ["html"]},
    {"name": "parse", "depends_on": ["fetch"], "requires": ["html"], "produces": ["items"]},
    {"name": "store", "depends_on": ["parse"], "requires": ["items"], "produces": ["stored"]},
)

_DEFAULT_BASE_DIR = os.environ.get("CRAWLFLOW_HOME", ".crawlflow")
_BASE_DIR = _DEFAULT_BASE_DIR

_HANDLERS = {}


class FlowError(Exception):
    """流程定义、上下文或执行错误。"""


def set_base_dir(path):
    """设置产物与令牌落盘根目录；传 None 恢复默认。"""
    global _BASE_DIR
    _BASE_DIR = path or _DEFAULT_BASE_DIR


def register_handler(name, func):
    """注册阶段处理函数：func(context) -> dict（产物键值，需可 JSON 序列化）。"""
    if not name or not isinstance(name, str):
        raise FlowError("handler name must be a non-empty string")
    if not callable(func):
        raise FlowError("handler for stage '%s' is not callable" % name)
    _HANDLERS[name] = func


def load_flow(path):
    """从 JSON 配置文件加载流程定义。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            flow = json.load(fh)
    except OSError as exc:
        raise FlowError("cannot read flow config %s: %s" % (path, exc))
    except ValueError as exc:
        raise FlowError("invalid JSON in flow config %s: %s" % (path, exc))
    if not isinstance(flow, dict):
        raise FlowError("flow config %s must contain a JSON object" % path)
    return _normalize_flow(flow)


def run(flow, context, token_ttl=DEFAULT_TOKEN_TTL):
    """按依赖拓扑执行流程，返回阶段状态、重跑列表、统计和恢复令牌。"""
    flow = _normalize_flow(flow)
    context = dict(context or {})
    _check_serializable(context)
    order = _topo_order(flow["stages"])
    _check_handlers(flow)
    _check_requirements(flow, order, context)
    run_id = uuid.uuid4().hex[:12]
    outcome = _execute(flow, order, context, run_id, prior=None)
    token = uuid.uuid4().hex
    _save_token(token, flow, outcome["context"], run_id, outcome["statuses"],
                outcome["stats"], time.time() + float(token_ttl))
    return {
        "run_id": run_id,
        "token": token,
        "status": outcome["status"],
        "stages": outcome["rows"],
        "rerun": outcome["rerun"],
        "stats": outcome["stats"],
        "context": outcome["context"],
    }


def resume(token):
    """凭令牌恢复：只重跑失败/被跳过的阶段及其下游，复用已成功阶段的产物。"""
    payload = _load_token(token)
    if payload is None:
        return {"token": token, "resumed": False, "stage": "fetch",
                "error": "unknown token"}
    flow = payload["flow"]
    order = _topo_order(flow["stages"])
    if time.time() > payload["expires_at"]:
        return {"token": token, "resumed": False, "stage": order[0],
                "error": "token expired"}
    outcome = _execute(flow, order, dict(payload["context"]), payload["run_id"],
                       prior=payload)
    _save_token(token, flow, outcome["context"], payload["run_id"],
                outcome["statuses"], outcome["stats"], payload["expires_at"])
    first = outcome["rerun"][0] if outcome["rerun"] else order[0]
    return {
        "token": token,
        "resumed": True,
        "stage": first,
        "run_id": payload["run_id"],
        "status": outcome["status"],
        "stages": outcome["rows"],
        "rerun": outcome["rerun"],
        "stats": outcome["stats"],
        "context": outcome["context"],
    }


def list_artifacts(run_id):
    """列出某次运行落盘的产物：{阶段名: [产物名, ...]}。"""
    base = os.path.join(_run_dir(run_id), "artifacts")
    out = {}
    if not os.path.isdir(base):
        return out
    for stage in sorted(os.listdir(base)):
        stage_dir = os.path.join(base, stage)
        if os.path.isdir(stage_dir):
            out[stage] = sorted(
                f[:-5] for f in os.listdir(stage_dir) if f.endswith(".json"))
    return out


def get_artifact(run_id, stage, name):
    """读取某个阶段的产物值；不存在则抛 FlowError。"""
    path = os.path.join(_artifact_dir(run_id, stage), "%s.json" % name)
    if not os.path.exists(path):
        raise FlowError("artifact not found: %s/%s/%s" % (run_id, stage, name))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["value"]


# ---------------------------------------------------------------- 内部实现

def _normalize_flow(flow):
    if isinstance(flow, str):
        return load_flow(flow)
    if not flow or not flow.get("stages"):
        return {"name": "default", "stages": [dict(s) for s in _DEFAULT_STAGES]}
    stages = []
    for raw in flow["stages"]:
        if not isinstance(raw, dict) or not raw.get("name"):
            raise FlowError("every stage needs a name")
        stages.append({
            "name": str(raw["name"]),
            "depends_on": [str(d) for d in raw.get("depends_on", [])],
            "requires": [str(r) for r in raw.get("requires", [])],
            "produces": [str(p) for p in raw.get("produces", [])],
        })
    return {"name": flow.get("name", "flow"), "stages": stages}


def _topo_order(stages):
    """Kahn 按入度拓扑排序；重名、未知依赖、成环直接拒绝。"""
    names = [s["name"] for s in stages]
    if len(set(names)) != len(names):
        dup = sorted({n for n in names if names.count(n) > 1})
        raise FlowError("duplicate stage name: %s" % ", ".join(dup))
    known = set(names)
    indeg = {n: 0 for n in names}
    children = {n: [] for n in names}
    for s in stages:
        for dep in s["depends_on"]:
            if dep not in known:
                raise FlowError(
                    "stage '%s' depends on unknown stage '%s'" % (s["name"], dep))
            indeg[s["name"]] += 1
            children[dep].append(s["name"])
    ready = [n for n in names if indeg[n] == 0]
    order = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for child in children[node]:
            indeg[child] -= 1
            if indeg[child] == 0:
                ready.append(child)
    if len(order) != len(names):
        remaining = [n for n in names if n not in order]
        raise FlowError(
            "dependency cycle detected among stages: %s" % ", ".join(remaining))
    return order


def _check_handlers(flow):
    for s in flow["stages"]:
        if s["name"] not in _HANDLERS:
            raise FlowError(
                "unknown stage '%s': no handler registered" % s["name"])


def _check_requirements(flow, order, context):
    """静态检查：每个阶段的 requires 能由初始上下文或上游产物满足。"""
    by_name = {s["name"]: s for s in flow["stages"]}
    available = set(context)
    for name in order:
        spec = by_name[name]
        missing = [k for k in spec["requires"] if k not in available]
        if missing:
            raise FlowError("stage '%s' missing context fields: %s"
                            % (name, ", ".join(missing)))
        available.update(spec["produces"])


def _check_serializable(context):
    try:
        json.dumps(context)
    except (TypeError, ValueError) as exc:
        raise FlowError("context is not JSON-serializable: %s" % exc)


def _execute(flow, order, context, run_id, prior):
    """执行一轮；prior 非空时跳过已成功阶段，重跑其余阶段并清理其产物。"""
    by_name = {s["name"]: s for s in flow["stages"]}
    statuses = dict(prior["statuses"]) if prior else {}
    stats = {n: dict(v) for n, v in (prior["stats"].items() if prior else [])}
    for name in order:
        statuses.setdefault(name, "pending")
        stats.setdefault(name, {"duration": 0.0, "reruns": 0})
    rows = []
    rerun = []
    for name in order:
        spec = by_name[name]
        if statuses[name] == "ok":
            rows.append({"stage": name, "status": "ok",
                         "duration": 0.0, "cached": True})
            continue
        if any(statuses[d] != "ok" for d in spec["depends_on"]):
            statuses[name] = "skipped"
            rows.append({"stage": name, "status": "skipped", "duration": 0.0})
            continue
        missing = [k for k in spec["requires"] if k not in context]
        if missing:
            statuses[name] = "failed"
            rows.append({"stage": name, "status": "failed",
                         "error": "missing context fields: %s" % ", ".join(missing),
                         "duration": 0.0})
            continue
        if prior is not None:
            _clean_artifacts(run_id, name)
            stats[name]["reruns"] += 1
            rerun.append(name)
        start = time.monotonic()
        try:
            produced = _HANDLERS[name](context) or {}
        except Exception as exc:  # 阶段异常转为失败状态，不炸掉整个编排
            elapsed = time.monotonic() - start
            stats[name]["duration"] += elapsed
            statuses[name] = "failed"
            rows.append({"stage": name, "status": "failed",
                         "error": str(exc), "duration": elapsed})
            continue
        elapsed = time.monotonic() - start
        if not isinstance(produced, dict):
            raise FlowError(
                "stage '%s' handler must return a dict" % name)
        try:
            json.dumps(produced)
        except (TypeError, ValueError):
            raise FlowError(
                "stage '%s' produced non-serializable output" % name)
        stats[name]["duration"] += elapsed
        context.update(produced)
        _write_artifacts(run_id, name, produced)
        statuses[name] = "ok"
        rows.append({"stage": name, "status": "ok", "duration": elapsed})
    status = "ok" if all(statuses[n] == "ok" for n in order) else "failed"
    return {"rows": rows, "rerun": rerun, "stats": stats, "statuses": statuses,
            "status": status, "context": context}


def _run_dir(run_id):
    return os.path.join(_BASE_DIR, "runs", run_id)


def _artifact_dir(run_id, stage):
    return os.path.join(_run_dir(run_id), "artifacts", stage)


def _token_path(token):
    return os.path.join(_BASE_DIR, "tokens", "%s.json" % token)


def _write_artifacts(run_id, stage, produced):
    stage_dir = _artifact_dir(run_id, stage)
    os.makedirs(stage_dir, exist_ok=True)
    for key, value in produced.items():
        with open(os.path.join(stage_dir, "%s.json" % key), "w",
                  encoding="utf-8") as fh:
            json.dump({"stage": stage, "name": key, "value": value},
                      fh, ensure_ascii=False, indent=2)


def _clean_artifacts(run_id, stage):
    shutil.rmtree(_artifact_dir(run_id, stage), ignore_errors=True)


def _save_token(token, flow, context, run_id, statuses, stats, expires_at):
    payload = {
        "token": token,
        "flow": flow,
        "context": context,
        "run_id": run_id,
        "statuses": statuses,
        "stats": stats,
        "saved_at": time.time(),
        "expires_at": expires_at,
    }
    path = _token_path(token)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _load_token(token):
    if not token or os.path.basename(str(token)) != str(token):
        return None
    path = _token_path(str(token))
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 内置阶段

def _fetch(context):
    return {"html": "<html>%s</html>" % context.get("seed", "")}


def _parse(context):
    html = context.get("html", "")
    return {"items": [html] if html else []}


def _store(context):
    return {"stored": len(context.get("items", []))}


register_handler("fetch", _fetch)
register_handler("parse", _parse)
register_handler("store", _store)
