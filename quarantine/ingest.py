"""坏数据隔离与回放。

ingest 不再整批回滚：好行正常入库，坏行连同原始内容和原因进入隔离区；
隔离区按批次和原因建索引，支持修复后按批幂等重放，并与原批次对账。
纯标准库实现，状态保存在模块级结构里，可用 reset 清空（测试用）。
"""

import copy
import json
from collections import Counter
from datetime import datetime, timezone

# 入库行：row_key -> 原始行内容（深拷贝，调用方后续修改不影响库里的数据）
_store = {}
# 隔离区记录：record_id -> 记录
_quarantine = {}
# 批次元信息：batch_id -> {total, ingested, quarantined, reason_dist}
_batches = {}
# 批次索引：batch_id -> [record_id, ...]
_batch_index = {}
# 原因索引：reason -> [record_id, ...]
_reason_index = {}

_seq = 0


def _row_key(row):
    """行幂等键：优先用 id，否则用整行内容的稳定哈希。"""
    if isinstance(row, dict) and "id" in row:
        return "id:%s" % row["id"]
    return "hash:%s" % hash(json.dumps(row, sort_keys=True, default=str))


def _check(row):
    """返回坏行原因；好行返回 None。"""
    if not isinstance(row, dict):
        return "not_a_dict"
    if row.get("bad"):
        return "flagged_bad"
    if row.get("id") is None:
        return "missing_id"
    return None


def _index(record_id, batch_id, reason):
    _batch_index.setdefault(batch_id, []).append(record_id)
    _reason_index.setdefault(reason, []).append(record_id)


def _now():
    return datetime.now(timezone.utc).isoformat()


def ingest(rows, batch_id=None, legacy=False):
    """入库一批数据并隔离坏行。

    好行写入内存库（按行幂等，重复内容只算一次入库）；坏行原样保留进隔离区，
    记录批次、原因、原始内容和序号，不再整批回滚。

    legacy=True 保留历史语义：遇到第一条坏行即抛 ValueError 且不入库。
    返回入库数、隔离数、批次号和原因分布。
    """
    global _seq

    if legacy:
        for row in rows:
            reason = _check(row)
            if reason is not None:
                raise ValueError("bad row at %s" % (row.get("id") if isinstance(row, dict) else row))
        for row in rows:
            _store[_row_key(row)] = copy.deepcopy(row)
        return {"ingested": len(rows), "quarantined": 0}

    if batch_id is None:
        _seq += 1
        batch_id = "batch-%d" % _seq

    ingested = 0
    quarantined = 0
    reason_dist = Counter()

    for position, row in enumerate(rows):
        reason = _check(row)
        if reason is None:
            key = _row_key(row)
            if key not in _store:
                _store[key] = copy.deepcopy(row)
                ingested += 1
            continue

        record_id = "q-%d" % (len(_quarantine) + 1)
        _quarantine[record_id] = {
            "record_id": record_id,
            "batch_id": batch_id,
            "reason": reason,
            "position": position,
            "row_key": _row_key(row),
            "raw": copy.deepcopy(row),
            "status": "quarantined",
            "replayed_at": None,
        }
        _index(record_id, batch_id, reason)
        quarantined += 1
        reason_dist[reason] += 1

    _batches[batch_id] = {
        "batch_id": batch_id,
        "total": len(rows),
        "ingested": ingested,
        "quarantined": quarantined,
        "reason_dist": dict(reason_dist),
    }
    return {
        "batch_id": batch_id,
        "ingested": ingested,
        "quarantined": quarantined,
        "reason_dist": dict(reason_dist),
    }


def replay(batch_id, fixed_rows=None):
    """按批次重放隔离区中的坏行，并与原批次对账。

    fixed_rows 可传入修复后的行，匹配优先级：(record_id, 行)、(位置, 行)、
    按 row_key/id 匹配的 dict，剩余 dict 按隔离顺序与未重放记录对齐；
    不传则直接用隔离区保留的原始内容重放。重放幂等：已入库的行不会重复。
    仍不合格的行继续留在隔离区，原因刷新为最新校验结果。
    未知批次抛 NotImplementedError（保持历史口径）。
    """
    if batch_id not in _batch_index:
        raise NotImplementedError("replay not supported for unknown batch: %s" % batch_id)

    fixed_by_position, fixed_by_key, fixed_by_id, fixed_by_record = {}, {}, {}, {}
    plain_rows = []
    for item in fixed_rows or []:
        if isinstance(item, tuple) and len(item) == 2:
            marker, row = item
            if isinstance(marker, int):
                # (批次内位置, 修复行) 或 (隔离记录序号, 修复行) 都按位置处理
                fixed_by_position[marker] = row
            else:
                fixed_by_record[marker] = row
        elif isinstance(item, dict):
            plain_rows.append(item)
            fixed_by_key[_row_key(item)] = item
            if item.get("id") is not None:
                fixed_by_id[item["id"]] = item

    original = _batches[batch_id]
    replayed = 0
    duplicates = 0
    still_bad = 0
    reason_dist = Counter()

    pending_records = [
        rid
        for rid in _batch_index[batch_id]
        if _quarantine[rid]["status"] != "replayed"
    ]
    # 仅给字典形式的修复行：按隔离顺序与未重放记录一一对齐，
    # 覆盖原始坏行没有 id、无法按 id/key 匹配的场景
    aligned = {}
    if plain_rows:
        for record_id, row in zip(pending_records, plain_rows):
            aligned[record_id] = row

    for record_id in _batch_index[batch_id]:
        record = _quarantine[record_id]
        if record["status"] == "replayed":
            duplicates += 1
            continue

        row = fixed_by_record.get(record_id)
        if row is None:
            row = fixed_by_position.get(record["position"])
        if row is None:
            row = aligned.get(record_id)
        if row is None:
            row = fixed_by_key.get(record["row_key"])
        if row is None:
            raw = record["raw"]
            if isinstance(raw, dict) and raw.get("id") is not None:
                row = fixed_by_id.get(raw["id"], raw)
            else:
                row = raw

        reason = _check(row)
        if reason is not None:
            record["raw"] = copy.deepcopy(row)
            record["reason"] = reason
            still_bad += 1
            reason_dist[reason] += 1
            continue

        key = _row_key(row)
        if key in _store:
            record["status"] = "replayed"
            record["replayed_at"] = _now()
            duplicates += 1
            continue

        _store[key] = copy.deepcopy(row)
        record["status"] = "replayed"
        record["replayed_at"] = _now()
        replayed += 1

    result = {
        "batch_id": batch_id,
        "replayed": replayed,
        "duplicates": duplicates,
        "still_bad": still_bad,
        "reason_dist": dict(reason_dist),
    }
    result["reconciliation"] = _reconcile(batch_id, original, result)
    return result


def _reconcile(batch_id, original, replay_result):
    """重放结果与原批次对账。"""
    outstanding = sum(
        1
        for record_id in _batch_index[batch_id]
        if _quarantine[record_id]["status"] != "replayed"
    )
    return {
        "original_total": original["total"],
        "original_ingested": original["ingested"],
        "original_quarantined": original["quarantined"],
        "replayed_ok": replay_result["replayed"],
        "duplicates": replay_result["duplicates"],
        "still_quarantined": outstanding,
        # 原批好行 + 重放新入库 + 重复跳过 + 仍隔离 = 原批总行数时对账通过
        "balanced": (
            original["ingested"]
            + replay_result["replayed"]
            + replay_result["duplicates"]
            + outstanding
            == original["total"]
        ),
    }


def quarantine_list(batch_id=None, reason=None):
    """查询隔离区，可按批次和/或原因过滤。"""
    if batch_id is not None:
        ids = list(_batch_index.get(batch_id, ()))
        if reason is not None:
            reason_ids = set(_reason_index.get(reason, ()))
            ids = [rid for rid in ids if rid in reason_ids]
    elif reason is not None:
        ids = list(_reason_index.get(reason, ()))
    else:
        ids = list(_quarantine)
    return [copy.deepcopy(_quarantine[rid]) for rid in ids]


def export_samples(reason, limit=None):
    """按原因导出隔离区样本（原始内容），limit 限制条数。"""
    samples = [
        copy.deepcopy(_quarantine[rid]["raw"])
        for rid in _reason_index.get(reason, ())
    ]
    return samples[:limit] if limit is not None else samples


def reset():
    """清空所有状态（测试用）。"""
    global _seq
    _store.clear()
    _quarantine.clear()
    _batches.clear()
    _batch_index.clear()
    _reason_index.clear()
    _seq = 0
