"""变更批处理：整批原子应用，失败整批回滚，重复批次按位点跳过。

不变量：
- 批次按 seq 顺序推进，只有 seq == watermark + 1 的批次可以应用；
  越位批次（seq 更大）进入 pending 阻塞，不会在水线上留缺口。
- 只有整批成功才推进水线；失败批次不推进水线。
- 失败时按写入的逆序回滚，表恢复到批次开始前的状态。
- 同 id 已应用，或 seq <= watermark，视为重复提交：整批跳过，只应用一次。
- 冲突行保留两侧：已提交的旧值和本次新值都写进 conflicts 明细，
  表内落新值，回滚时旧值按逆序还回去。
- 应用、回滚、跳过、阻塞、恢复都写审计日志；回滚明细可导出回看。

批次格式::

    {"id": "b1", "seq": 1,
     "rows": [{"key": "a", "value": 10}, ...],
     "fail_times": 0}   # 演示/测试用：前 N 次提交注入写入故障

行级 ``{"bad": True}`` 模拟该行写入失败（永久性坏数据）。
"""

import copy
import json

_STATE = None


def reset():
    """清空表、水线、提交次数、待恢复批次和审计日志。"""
    global _STATE
    _STATE = {
        "table": {},
        "watermark": 0,
        "applied": {},   # batch id -> seq
        "attempts": {},  # batch id -> 提交次数
        "pending": {},   # batch id -> 批次（失败或越位待恢复）
        "journal": [],
        "tick": 0,
    }


reset()


def _audit(event, **fields):
    _STATE["tick"] += 1
    entry = {"event": event, "tick": _STATE["tick"]}
    entry.update(fields)
    _STATE["journal"].append(entry)
    return entry


def _result(batch, seq, status, applied=0, rolled_back=0, skipped=0,
            conflicts=None, rows=0, **extra):
    out = {
        "batch": batch,
        "seq": seq,
        "status": status,
        "rows": rows,
        "applied": applied,
        "rolled_back": rolled_back,
        "skipped": skipped,
        "conflicts": conflicts or [],
        "watermark": _STATE["watermark"],
    }
    out.update(extra)
    return out


def _row_key(row, index):
    if not isinstance(row, dict):
        raise ValueError("第 %d 行不是对象" % index)
    key = row.get("key", row.get("id"))
    if key is None:
        raise ValueError("第 %d 行缺少 key" % index)
    return key


def apply(batch):
    """应用一个批次。整批成功才推进水线，任何一行失败则整批回滚。"""
    if not isinstance(batch, dict):
        raise ValueError("批次必须是对象")
    bid = batch.get("id")
    if not bid:
        raise ValueError("批次缺少 id")
    rows = list(batch.get("rows") or [])
    # 结构错误在动表之前就拒绝，避免半批状态。
    for index, row in enumerate(rows):
        _row_key(row, index)
    seq = int(batch.get("seq", _STATE["watermark"] + 1))
    if seq < 1:
        raise ValueError("seq 必须 >= 1")

    # 重复提交：同 id 已应用，或位点已被成功批次覆盖 -> 整批跳过。
    if bid in _STATE["applied"]:
        _STATE["pending"].pop(bid, None)
        reason = "batch id already applied"
        _audit("skipped_duplicate", batch=bid, seq=seq, rows=len(rows),
               reason=reason)
        return _result(bid, seq, "skipped", skipped=len(rows),
                       rows=len(rows), reason=reason)
    if seq <= _STATE["watermark"]:
        _STATE["pending"].pop(bid, None)
        reason = "position <= watermark"
        _audit("skipped_duplicate", batch=bid, seq=seq, rows=len(rows),
               reason=reason)
        return _result(bid, seq, "skipped", skipped=len(rows),
                       rows=len(rows), reason=reason)

    # 越位：前面还有批次没成功，先占位等待，避免水线出现缺口。
    if seq > _STATE["watermark"] + 1:
        _STATE["pending"].setdefault(bid, copy.deepcopy(batch))
        reason = "out of order: expected seq %d" % (_STATE["watermark"] + 1)
        _audit("blocked", batch=bid, seq=seq, rows=len(rows), reason=reason)
        return _result(bid, seq, "blocked", rows=len(rows), reason=reason)

    attempt = _STATE["attempts"].get(bid, 0) + 1
    _STATE["attempts"][bid] = attempt
    inject_fail = attempt <= int(batch.get("fail_times", 0))

    table = _STATE["table"]
    undo = []        # 已写入的信息，用于逆序回滚
    written = set()  # 本批次内已写过的 key
    conflicts = []
    failure = None

    try:
        for index, row in enumerate(rows):
            key = _row_key(row, index)
            if row.get("bad"):
                raise RuntimeError("第 %d 行（key=%s）写入失败" % (index, key))
            if inject_fail and index == len(rows) - 1:
                raise RuntimeError("第 %d 行（key=%s）写入失败：注入故障"
                                   % (index, key))
            value = copy.deepcopy(row.get("value"))
            # 与已提交值不同才算冲突；本批次内先写后改不算冲突。
            if key not in written and key in table and table[key] != value:
                conflicts.append({
                    "key": key,
                    "existing": copy.deepcopy(table[key]),
                    "incoming": copy.deepcopy(value),
                    "row_index": index,
                    "resolution": "took incoming",
                })
            undo.append({
                "key": key,
                "value": copy.deepcopy(value),
                "prior_present": key in table,
                "prior": copy.deepcopy(table.get(key)),
            })
            table[key] = value
            written.add(key)
    except Exception as exc:  # noqa: BLE001 - 任意行失败都要整批回滚
        failure = exc

    if failure is not None:
        restored = []
        for step in reversed(undo):
            if step["prior_present"]:
                table[step["key"]] = step["prior"]
            else:
                table.pop(step["key"], None)
            restored.append({
                "key": step["key"],
                "attempted": step["value"],
                "restored": copy.deepcopy(step["prior"]),
                "restored_present": step["prior_present"],
            })
        # 本批已回滚，冲突里“已落新值”的说法不再成立，按回滚改写。
        for conflict in conflicts:
            conflict["resolution"] = "reverted with batch rollback"
        detail = {
            "batch": bid,
            "seq": seq,
            "attempt": attempt,
            "error": str(failure),
            "rows": restored,
        }
        _STATE["pending"][bid] = copy.deepcopy(batch)
        _audit("rolled_back", **detail)
        return _result(bid, seq, "rolled_back", applied=0,
                       rolled_back=len(restored), conflicts=conflicts,
                       rows=len(rows), error=str(failure), rollback=detail)

    _STATE["watermark"] = seq
    _STATE["applied"][bid] = seq
    _STATE["pending"].pop(bid, None)
    _audit("applied", batch=bid, seq=seq, rows=len(rows),
           written=len(undo), conflicts=len(conflicts))
    return _result(bid, seq, "applied", applied=len(undo),
                   conflicts=conflicts, rows=len(rows))


def resume(token=None):
    """从最后成功批之后继续：按 seq 顺序重试待恢复批次。

    只应用紧接水线的批次；遇到再次失败或仍有缺口的批次即停止，
    不会越过它去应用后面的批次。重复调用是安全的：已成功的批次
    由 ``apply`` 按重复提交跳过。``token`` 作为本次恢复的标签写入审计。
    """
    applied = rolled_back = skipped = 0
    conflicts = []
    batches = []

    ordering = sorted(
        _STATE["pending"].items(),
        key=lambda item: (int(item[1].get("seq", _STATE["watermark"])), item[0]),
    )
    for _bid, batch in ordering:
        seq = int(batch.get("seq", _STATE["watermark"] + 1))
        if seq > _STATE["watermark"] + 1:
            break  # 缺口未补，后面的批次继续等待
        result = apply(batch)  # 已覆盖的待恢复批次由 apply 按重复提交清掉
        applied += result["applied"]
        rolled_back += result["rolled_back"]
        skipped += result["skipped"]
        conflicts.extend(result["conflicts"])
        batches.append(result)
        if result["status"] not in ("applied", "skipped"):
            break

    complete = not _STATE["pending"]
    _audit("resumed", token=token, complete=complete, applied=applied,
           rolled_back=rolled_back, skipped=skipped,
           batches=[b["batch"] for b in batches])
    return {
        "token": token,
        "resumed": complete,
        "applied": applied,
        "rolled_back": rolled_back,
        "skipped": skipped,
        "conflicts": conflicts,
        "batches": batches,
        "watermark": _STATE["watermark"],
    }


def snapshot():
    """当前已提交表的副本，用于对账。"""
    return copy.deepcopy(_STATE["table"])


def watermark():
    """最后成功推进的位点。"""
    return _STATE["watermark"]


def pending():
    """尚未恢复成功的批次副本。"""
    return {bid: copy.deepcopy(batch)
            for bid, batch in _STATE["pending"].items()}


def journal():
    """审计日志副本：applied / rolled_back / skipped_duplicate / blocked / resumed。"""
    return copy.deepcopy(_STATE["journal"])


def export_rollbacks(path=None):
    """导出回滚明细；传入 path 时同时写成 UTF-8 JSON 文件。"""
    entries = [copy.deepcopy(entry) for entry in _STATE["journal"]
               if entry["event"] == "rolled_back"]
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(entries, handle, ensure_ascii=False, indent=2)
    return entries
