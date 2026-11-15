"""行级修复与回放：坏行隔离保留原文，好行入库，修复后按批幂等回放并对账。"""

import json

MAX_BAD_RATIO = 0.5  # 坏行比例达到该值时整批暂停

_batches = {}    # batch_id -> {"rows": 原始行, "good": 好行, "status": ...}
_quarantine = []  # {"qid", "batch", "index", "row", "reason", "repaired"}
_store = {}      # id -> row，按 id 幂等去重，重复回放不产生重复行
_batch_seq = [0]
_qid_seq = [0]
_last = [None]


def reset():
    """清空全部状态（批次、隔离区、库）。"""
    _batches.clear()
    _quarantine.clear()
    _store.clear()
    _batch_seq[0] = 0
    _qid_seq[0] = 0
    _last[0] = None


def _validate(row):
    """返回坏行原因，好行返回 None。"""
    if not isinstance(row, dict):
        return "not_a_mapping"
    if "id" not in row:
        return "missing_id"
    if row.get("ok", True) is False:
        return "flagged_bad"
    return None


def _resolve(batch):
    return _last[0] if batch == "last" else batch


def ingest(rows):
    """好行入库，坏行带原文和原因进隔离区；坏行超比例时整批暂停。"""
    _batch_seq[0] += 1
    batch = "batch-%d" % _batch_seq[0]
    rows = list(rows)
    good, bad = [], []
    for i, row in enumerate(rows):
        reason = _validate(row)
        if reason is None:
            good.append(row)
        else:
            bad.append((i, row, reason))
    for i, row, reason in bad:
        _qid_seq[0] += 1
        _quarantine.append({"qid": _qid_seq[0], "batch": batch, "index": i,
                            "row": row, "reason": reason, "repaired": False})
    paused = bool(bad) and len(bad) / len(rows) >= MAX_BAD_RATIO
    stored = []
    if not paused:
        for row in good:
            if row["id"] not in _store:
                _store[row["id"]] = row
                stored.append(row)
    _batches[batch] = {"rows": rows, "good": good,
                       "status": "paused" if paused else
                                 ("partial" if bad else "stored")}
    _last[0] = batch
    return {"batch": batch, "stored": stored,
            "isolated": [e for e in _quarantine if e["batch"] == batch],
            "rolled_back": paused, "paused": paused}


def fix(qid, new_row):
    """修复隔离区中的一行，回放时重新校验。未知 qid 返回 None。"""
    for entry in _quarantine:
        if entry["qid"] == qid:
            entry["row"] = new_row
            entry["repaired"] = True
            return entry
    return None


def _reconcile(batch_id):
    """按批对账：每行要么在库、要么在隔离区，隔离按原因归类。"""
    rec = _batches[batch_id]
    total = len(rec["rows"])
    stored = sum(1 for r in rec["rows"]
                 if isinstance(r, dict) and "id" in r and r["id"] in _store)
    remaining = [e for e in _quarantine if e["batch"] == batch_id]
    by_reason = {}
    for e in remaining:
        by_reason[e["reason"]] = by_reason.get(e["reason"], 0) + 1
    return {"expected": total, "stored": stored,
            "quarantined": len(remaining), "missing": total - stored - len(remaining),
            "by_reason": by_reason}


def _replay_one(batch_id):
    rec = _batches.get(batch_id)
    if rec is None:
        return {"batch": batch_id, "replayed": 0, "inserted": 0, "skipped": 0,
                "matched": False, "paused": False, "error": "unknown_batch",
                "reconciliation": {}}
    inserted = 0
    # 重新校验本批隔离区（含已修复行），通过即入库，仍坏则更新原因、保留现场
    for entry in [e for e in _quarantine if e["batch"] == batch_id]:
        reason = _validate(entry["row"])
        if reason is None:
            rid = entry["row"]["id"]
            if rid not in _store:
                _store[rid] = entry["row"]
                inserted += 1
            _quarantine.remove(entry)
        else:
            entry["reason"] = reason
    # 暂停批：坏行比例仍达标则保留现场继续暂停，否则放行暂存的好行
    if rec["status"] == "paused":
        total = len(rec["rows"])
        remaining = [e for e in _quarantine if e["batch"] == batch_id]
        if remaining and len(remaining) / total >= MAX_BAD_RATIO:
            recon = _reconcile(batch_id)
            return {"batch": batch_id, "replayed": recon["stored"],
                    "inserted": inserted, "skipped": 0, "matched": False,
                    "paused": True, "reconciliation": recon}
        for row in rec["good"]:
            if row["id"] not in _store:
                _store[row["id"]] = row
                inserted += 1
        rec["status"] = "replayed"
    recon = _reconcile(batch_id)
    if recon["quarantined"] == 0 and rec["status"] != "stored":
        rec["status"] = "replayed"
    replayed = recon["stored"]
    return {"batch": batch_id, "replayed": replayed, "inserted": inserted,
            "skipped": replayed - inserted,
            "matched": recon["missing"] == 0 and recon["quarantined"] == 0,
            "paused": False, "reconciliation": recon}


def replay(batch):
    """按批幂等回放：已入库不重复；支持批次列表做部分批次回放。"""
    if isinstance(batch, (list, tuple)):
        parts = [_replay_one(_resolve(b)) for b in batch]
        return {"batch": list(batch),
                "replayed": sum(p["replayed"] for p in parts),
                "inserted": sum(p["inserted"] for p in parts),
                "skipped": sum(p["skipped"] for p in parts),
                "matched": all(p["matched"] for p in parts),
                "paused": any(p["paused"] for p in parts),
                "parts": parts}
    return _replay_one(_resolve(batch))


def progress(batch=None):
    """回放进度：每批的总数、入库、隔离、待定和状态。"""
    ids = [_resolve(batch)] if batch is not None else sorted(_batches)
    out = {}
    for b in ids:
        rec = _batches.get(b)
        if rec is None:
            continue
        recon = _reconcile(b)
        out[b] = {"status": rec["status"], "total": recon["expected"],
                  "stored": recon["stored"], "quarantined": recon["quarantined"],
                  "pending": recon["missing"]}
    return out


def export_quarantine(path=None):
    """导出隔离区数据（原始行、原因、批次号），可写 JSON 文件。"""
    data = [dict(e) for e in _quarantine]
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data
