"""小文件合并：按阈值选分区、跳过活跃读取、原子替换并同步清单。

分区结构（dict）：
    name: 分区名
    files: [{"path": ..., "size": ...}, ...]   当前小文件
    manifest: [path, ...]                      下游读取的清单
    active_reads: int                          活跃读取租约数
    history: [{"files": [...], "batch_id":..., "replaced_at": ts}]  替换下来的原文件
    fail_merge: bool                           注入故障（演示/测试用）
"""

import time
import uuid

DEFAULT_MAX_AVG_FILE_BYTES = 8 * 1024 * 1024
DEFAULT_MIN_FILES = 2
DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600


def _stats(part):
    files = part.get("files", [])
    total = sum(f.get("size", 0) for f in files)
    count = len(files)
    return {
        "count": count,
        "bytes": total,
        "avg_bytes": total // count if count else 0,
    }


def plan(dataset, max_avg_file_bytes=DEFAULT_MAX_AVG_FILE_BYTES,
         min_files=DEFAULT_MIN_FILES, only=None):
    """按分区统计文件数和大小，小于阈值才合并；活跃读取的分区跳过。

    only: 只对这些分区试运行（先在一个小分区验证替换和清单更新）。
    """
    selected, skipped, stats = [], [], {}
    for part in dataset.get("partitions", []):
        name = part["name"]
        st = _stats(part)
        stats[name] = st
        if only is not None and name not in only:
            skipped.append({"partition": name, "reason": "not-in-trial"})
        elif part.get("active_reads", 0) > 0:
            skipped.append({"partition": name, "reason": "active-reads"})
        elif st["count"] < min_files:
            skipped.append({"partition": name, "reason": "too-few-files"})
        elif st["avg_bytes"] >= max_avg_file_bytes:
            skipped.append({"partition": name, "reason": "files-large-enough"})
        else:
            selected.append(name)
    return {
        "batch_id": uuid.uuid4().hex[:8],
        "partitions": dataset.get("partitions", []),
        "selected": selected,
        "skipped": skipped,
        "stats": stats,
    }


def compact(plan_data, now=None):
    """合并写新文件后整体替换，清单同步更新；失败保留原文件。

    合并前再次检查活跃读取（plan 之后可能有新查询进来）。
    返回合并、跳过、替换和失败清单，以及合并前后文件数对比。
    """
    now = time.time() if now is None else now
    batch = plan_data["batch_id"]
    parts = {p["name"]: p for p in plan_data.get("partitions", [])}
    merged, replaced, failed = [], [], []
    skipped = list(plan_data.get("skipped", []))
    counts = {}
    for name in plan_data.get("selected", []):
        part = parts[name]
        before = len(part.get("files", []))
        if part.get("active_reads", 0) > 0:
            skipped.append({"partition": name, "reason": "active-reads"})
            continue
        try:
            if part.get("fail_merge"):
                raise RuntimeError("merge write failed")
            total = sum(f.get("size", 0) for f in part["files"])
            new_file = {"path": "{}/merged-{}.dat".format(name, batch),
                        "size": total}
            # 原文件进历史（保留一段时间，可回退），再整体替换 + 更新清单
            part.setdefault("history", []).append({
                "files": list(part["files"]),
                "batch_id": batch,
                "replaced_at": now,
            })
            part["files"] = [new_file]
            part["manifest"] = [new_file["path"]]
            merged.append(name)
            replaced.append({"partition": name, "new_file": new_file["path"],
                             "old_files": before})
            counts[name] = {"before": before, "after": 1}
        except Exception as exc:  # 失败保留原文件，清单不动
            failed.append({"partition": name, "error": str(exc)})
            counts[name] = {"before": before, "after": before}
    return {"batch_id": batch, "merged": merged, "skipped": skipped,
            "replaced": replaced, "failed": failed, "file_counts": counts}


def retry_failed(dataset, failed, now=None):
    """失败批次单独重试：只对失败的分区重新 plan + compact。"""
    names = [f["partition"] for f in failed]
    sub = {"partitions": [p for p in dataset.get("partitions", [])
                          if p["name"] in names]}
    return compact(plan(sub), now=now)


def rollback(part, batch_id):
    """回退：从 history 恢复原文件并还原清单。"""
    history = part.get("history", [])
    for i in range(len(history) - 1, -1, -1):
        if history[i]["batch_id"] == batch_id:
            entry = history.pop(i)
            part["files"] = list(entry["files"])
            part["manifest"] = [f["path"] for f in entry["files"]]
            return True
    return False


def cleanup_history(partitions, retention_seconds=DEFAULT_RETENTION_SECONDS,
                    now=None):
    """历史文件保留一段时间，过期清理。返回被清理的 (分区, 批次) 清单。"""
    now = time.time() if now is None else now
    removed = []
    for part in partitions:
        keep = []
        for entry in part.get("history", []):
            if now - entry["replaced_at"] >= retention_seconds:
                removed.append({"partition": part["name"],
                                "batch_id": entry["batch_id"]})
            else:
                keep.append(entry)
        part["history"] = keep
    return removed
