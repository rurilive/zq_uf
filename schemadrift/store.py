"""按环境保存结构指纹基线，并留档差异报告。

目录布局（纯标准库，JSON 落盘）：
    <root>/<env>/<table>.json     基线指纹
    <root>/<env>/audit.jsonl      追加式差异报告留档

环境之间互不影响：同一张表在 prod / staging / dev 各有一份基线；
基线不匹配时只报对应环境的漂移。
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone

from .drift import diff
from .fingerprint import snapshot

DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".schemadrift")

_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z_.-]+")


def _safe_name(value):
    text = str(value).strip()
    if not text:
        raise ValueError("名称不能为空")
    return _SAFE_NAME_RE.sub("_", text)


def _now():
    return datetime.now(timezone.utc).isoformat()


def baseline_path(table, env, root=DEFAULT_ROOT):
    return os.path.join(root, _safe_name(env), _safe_name(table) + ".json")


def _atomic_write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temp_path, path)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def save_baseline(table, root=DEFAULT_ROOT, env=None):
    """保存一张表的基线指纹；环境取自快照或 env 参数。

    返回落盘位置与指纹，便于报告和留档对齐。
    """
    # snapshot() 对快照幂等，这里统一走一遍以支持 table 与 snapshot 两种入参。
    snap = snapshot(table, env=env)
    env_name = snap.get("env") or "default"
    path = baseline_path(snap["table"], env_name, root)
    payload = dict(snap)
    payload["saved_at"] = _now()
    _atomic_write_json(path, payload)
    return {"env": env_name, "table": snap["table"], "path": path, "fingerprint": snap["fingerprint"]}


def load_baseline(table, env, root=DEFAULT_ROOT):
    """读取指定环境的基线；不存在时返回 None。"""
    path = baseline_path(table, env, root)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def list_baselines(root=DEFAULT_ROOT):
    """列出所有环境下的基线，返回 [{env, table, fingerprint, saved_at}]。"""
    items = []
    if not os.path.isdir(root):
        return items
    for env_name in sorted(os.listdir(root)):
        env_dir = os.path.join(root, env_name)
        if not os.path.isdir(env_dir):
            continue
        for filename in sorted(os.listdir(env_dir)):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(env_dir, filename), encoding="utf-8") as stream:
                    payload = json.load(stream)
            except (OSError, ValueError):
                continue
            items.append({
                "env": payload.get("env", env_name),
                "table": payload.get("table"),
                "fingerprint": payload.get("fingerprint"),
                "saved_at": payload.get("saved_at"),
            })
    return items


def archive_report(report, root=DEFAULT_ROOT, env=None, extra=None):
    """把差异报告追加到对应环境的 audit.jsonl 留档。"""
    env_name = str(env or report.get("target_env") or report.get("base_env") or "default")
    path = os.path.join(root, _safe_name(env_name), "audit.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        "archived_at": _now(),
        "env": env_name,
        "table": report.get("table"),
        "level": report.get("level"),
        "has_drift": report.get("has_drift"),
        "summary": report.get("summary"),
        "changes": report.get("changes", []),
        "report": report,
    }
    if extra:
        record["extra"] = extra
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def check(table, env=None, root=DEFAULT_ROOT, archive=True):
    """线上结构对比该环境的基线，返回 time 报告并留档。

    基线不存在时视为“首次采集”，不报漂移，只提示保存基线。
    """
    env_name = str(env or table.get("env") or "default")
    live = snapshot(table, env=env_name)
    base = load_baseline(live["table"], env_name, root)
    if base is None:
        report = {
            "table": live["table"],
            "env": env_name,
            "base_env": None,
            "target_env": env_name,
            "baseline_missing": True,
            "has_drift": False,
            "level": None,
            "changes": [],
            "summary": {kind: 0 for kind in ("added", "removed", "type", "default")},
            "level_counts": {"low": 0, "medium": 0, "high": 0},
            "added": [],
            "removed": [],
            "note": "该环境尚无基线，请先 save_baseline()。",
        }
    else:
        report = dict(diff(base, live))
        report["env"] = env_name
        report["baseline_missing"] = False
    if archive:
        archive_report(report, root=root, env=env_name)
    return report
