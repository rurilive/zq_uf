"""链路查询与导出：按标识汇总各阶段耗时、最慢阶段、失败点和错误链。

重复重试可合并查看（merged），明细可导出（export_detail），事件流可回放（replay）。
"""

import json
import os
import time

from . import store
from .config import slow_threshold_ms


def _locate(trace_id, task=None, data_dir=None):
    """按标识找到所属任务和该标识的全部事件。"""
    d = store.resolve_dir(data_dir)
    if task:
        tasks = [task]
    else:
        tasks = [fn[:-len(".jsonl")] for fn in os.listdir(d) if fn.endswith(".jsonl")]
    for t in tasks:
        events = store.load(t, d)
        mine = [e for e in events if e.get("trace") == trace_id]
        if mine:
            return t, mine
    raise KeyError(f"trace not found: {trace_id}")


def _stages(events):
    """把 stage_start/stage_end 配对成阶段记录；只有开始没有结束的记为卡住。"""
    open_ = {}
    stages = []
    for e in events:
        if e["type"] == "stage_start":
            open_[(e.get("attempt"), e["stage"])] = e
        elif e["type"] == "stage_end":
            st = open_.pop((e.get("attempt"), e["stage"]), None)
            stages.append({
                "attempt": e.get("attempt"), "stage": e["stage"],
                "pid": st.get("pid") if st else None,
                "start_ts": st["ts"] if st else None,
                "elapsed_ms": e.get("elapsed_ms"),
                "ok": e["ok"], "error": e.get("error"),
            })
    for (attempt, name), st in open_.items():
        stages.append({
            "attempt": attempt, "stage": name, "pid": st.get("pid"),
            "start_ts": st["ts"], "elapsed_ms": None,
            "ok": False, "error": "stage not finished",
        })
    return stages


def query(trace_id, task=None, data_dir=None, slow_ms=None):
    """按标识汇总：各次尝试、各阶段耗时、最慢阶段、失败点、错误链。"""
    slow = slow_threshold_ms() if slow_ms is None else slow_ms
    task, events = _locate(trace_id, task, data_dir)
    stages = _stages(events)
    ends = [e for e in events if e["type"] == "end"]

    attempts = []
    for a in sorted({e.get("attempt", 1) for e in events}):
        a_ends = [e for e in ends if e.get("attempt") == a]
        attempts.append({
            "attempt": a,
            "ok": a_ends[-1]["ok"] if a_ends else None,
            "error": a_ends[-1].get("error") if a_ends else None,
            "stages": [s for s in stages if s["attempt"] == a],
        })

    merged = {}
    for name in dict.fromkeys(s["stage"] for s in stages):
        recs = [s for s in stages if s["stage"] == name]
        last = recs[-1]
        merged[name] = {
            "attempts": len(recs),
            "ok": last["ok"],
            "elapsed_ms": last["elapsed_ms"],
            "slow": last["elapsed_ms"] is not None and last["elapsed_ms"] > slow,
        }

    finished = [s for s in stages if s["elapsed_ms"] is not None]
    slowest = max(finished, key=lambda s: s["elapsed_ms"], default=None)

    error_chain = [
        {"attempt": s["attempt"], "stage": s["stage"], "error": s["error"]}
        for s in stages if not s["ok"]
    ]
    error_chain += [
        {"attempt": e.get("attempt"), "stage": None, "error": e["error"]}
        for e in ends if not e.get("ok") and e.get("error")
    ]

    failure_point = None
    for att in reversed(attempts):
        if att["ok"] is False:
            bad = next((s for s in att["stages"] if not s["ok"]), None)
            failure_point = {
                "attempt": att["attempt"],
                "stage": bad["stage"] if bad else None,
                "error": (bad or {}).get("error") or att["error"],
            }
            break

    return {
        "trace": trace_id, "task": task, "slow_threshold_ms": slow,
        "attempts": attempts, "merged": merged,
        "slowest": slowest and {
            "attempt": slowest["attempt"], "stage": slowest["stage"],
            "elapsed_ms": slowest["elapsed_ms"],
        },
        "failure_point": failure_point,
        "error_chain": error_chain,
    }


def export(trace_id, path=None, task=None, data_dir=None, slow_ms=None):
    """导出汇总报告（标识、各阶段耗时、失败点、错误链）为 JSON 文件，返回路径。"""
    report = query(trace_id, task=task, data_dir=data_dir, slow_ms=slow_ms)
    path = path or os.path.join(store.resolve_dir(data_dir), "export", f"{trace_id}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path


def export_detail(trace_id, path=None, task=None, data_dir=None):
    """导出明细（该标识的全部原始事件）为 JSON 文件，返回路径。"""
    _, events = _locate(trace_id, task, data_dir)
    path = path or os.path.join(store.resolve_dir(data_dir), "export", f"{trace_id}-detail.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    return path


def replay(trace_id, task=None, data_dir=None):
    """回放：按时间顺序返回一条链路的全部事件（含各次重试、各进程）。"""
    _, events = _locate(trace_id, task, data_dir)
    return sorted(events, key=lambda e: (e.get("ts", 0), e.get("pid", 0)))


def format_replay(trace_id, task=None, data_dir=None):
    """把回放事件流格式化成可读时间线。"""
    lines = []
    for e in replay(trace_id, task=task, data_dir=data_dir):
        ts = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
        ts += f".{int(e['ts'] % 1 * 1000):03d}"
        head = f"{ts} attempt={e.get('attempt')} pid={e.get('pid', '-')}"
        if e["type"] == "begin":
            lines.append(f"{head} begin task={e['task']} host={e.get('host', '-')}")
        elif e["type"] == "stage_start":
            lines.append(f"{head}   -> {e['stage']} start")
        elif e["type"] == "stage_end":
            mark = "ok" if e["ok"] else f"FAIL {e.get('error')}"
            lines.append(f"{head}   <- {e['stage']} {e['elapsed_ms']}ms {mark}")
        elif e["type"] == "end":
            mark = "ok" if e["ok"] else f"FAIL {e.get('error')}"
            lines.append(f"{head} end {mark}")
    return "\n".join(lines)
