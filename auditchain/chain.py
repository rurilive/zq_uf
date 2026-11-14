"""哈希链审计日志：每条记录携带前一条哈希与自身哈希，支持分段轮转、区间校验与断链定位。"""
import hashlib
import json
import os
from datetime import datetime, timezone

GENESIS = "0" * 64


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_of(seq, event, prev):
    body = _canonical({"seq": seq, "event": event, "prev": prev})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _manifest_path(path):
    return path + ".manifest.json"


def _load_manifest(path):
    if os.path.exists(_manifest_path(path)):
        with open(_manifest_path(path), encoding="utf-8") as handle:
            return json.load(handle)
    return {"segments": []}


def _save_manifest(path, manifest):
    with open(_manifest_path(path), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)


def _iter_lines(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield line


def _last_record(path):
    last = None
    for line in _iter_lines(path):
        last = line
    return json.loads(last) if last else None


def _empty_result():
    return {"ok": True, "checked": 0, "chain_length": 0,
            "range": None, "break": None, "breaks": []}


def append(event, path, max_bytes=None):
    """追加一条记录到链上；max_bytes 超限先轮转。返回 path。"""
    if max_bytes is not None and os.path.exists(path) and os.path.getsize(path) >= max_bytes:
        rotate(path)
    prev, seq = GENESIS, 1
    last = _last_record(path)
    if last is not None:
        prev, seq = last["hash"], last["seq"] + 1
    else:
        segments = _load_manifest(path)["segments"]
        if segments:
            prev, seq = segments[-1]["last"], segments[-1]["end"] + 1
    record = {"seq": seq, "event": event, "prev": prev}
    record["hash"] = _hash_of(seq, event, prev)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def rotate(path):
    """把当前段归档为 path.N，清单记录该段首尾哈希与序号区间。返回归档路径。"""
    records = [json.loads(line) for line in _iter_lines(path)]
    if not records:
        return None
    manifest = _load_manifest(path)
    rotated = "%s.%d" % (path, len(manifest["segments"]) + 1)
    os.replace(path, rotated)
    manifest["segments"].append({
        "file": os.path.basename(rotated),
        "start": records[0]["seq"],
        "end": records[-1]["seq"],
        "first": records[0]["hash"],
        "last": records[-1]["hash"],
    })
    _save_manifest(path, manifest)
    return rotated


def verify(start=None, end=None, path=None):
    """校验 [start, end] 区间（缺省全量），只重算区间内记录的哈希。"""
    if path is None:
        return _empty_result()
    breaks = []
    manifest = _load_manifest(path)
    base = os.path.dirname(path) or "."
    sources = [(seg, os.path.join(base, seg["file"])) for seg in manifest["segments"]]
    if os.path.exists(path):
        sources.append((None, path))

    chain_length = 0
    if sources:
        meta, fpath = sources[-1]
        if meta is not None:
            chain_length = meta["end"]
        else:
            last = _last_record(fpath)
            if last is not None:
                chain_length = last["seq"]
            elif len(sources) > 1:
                chain_length = sources[-2][0]["end"]

    if not chain_length:
        return _empty_result()
    lo = max(1, start) if start is not None else 1
    hi = min(end, chain_length) if end is not None else chain_length
    checked = _scan(sources, lo, hi, breaks) if lo <= hi else 0
    expected = hi - lo + 1 if lo <= hi else 0
    if checked != expected:
        breaks.append({"seq": None, "file": None,
                       "reason": "区间内记录缺失（期望 %d 条，实得 %d 条）" % (expected, checked)})
    return {
        "ok": not breaks,
        "checked": checked,
        "chain_length": chain_length,
        "range": [lo, hi],
        "break": breaks[0] if breaks else None,
        "breaks": breaks,
    }


def _scan(sources, lo, hi, breaks):
    """按链位置流式扫描，仅对 [lo, hi] 内记录重算哈希，边界各多读一条作锚点。"""
    checked = 0
    pos = 0
    prev_hash = None
    for meta, fpath in sources:
        name = meta["file"] if meta else os.path.basename(fpath)
        if meta and meta["end"] < lo - 1:
            if meta["start"] != pos + 1:
                breaks.append({"seq": pos + 1, "file": name,
                               "reason": "轮转清单起始序号与链位置不符"})
            pos = meta["end"]
            continue
        if meta and meta["start"] != pos + 1:
            breaks.append({"seq": pos + 1, "file": name,
                           "reason": "轮转清单起始序号与链位置不符"})
        rec_start = rec_end = None
        stopped = False
        for line in _iter_lines(fpath):
            pos += 1
            if pos > hi + 1:
                stopped = True
                break
            if not (lo - 1 <= pos <= hi + 1 or (meta and pos in (meta["start"], meta["end"]))):
                continue
            rec = json.loads(line)
            if meta and pos == meta["start"]:
                rec_start = rec
            if meta and pos == meta["end"]:
                rec_end = rec
            if rec.get("seq") != pos:
                breaks.append({"seq": pos, "file": name,
                               "reason": "记录序号与链位置不符"})
            if pos == lo - 1:
                prev_hash = rec.get("hash")
                continue
            if pos <= hi:
                expected_prev = GENESIS if pos == 1 else prev_hash
                if rec.get("prev") != expected_prev:
                    breaks.append({"seq": pos, "file": name,
                                   "reason": "prev 与上一条哈希不符，链断裂"})
                if _hash_of(rec.get("seq"), rec.get("event"), rec.get("prev")) != rec.get("hash"):
                    breaks.append({"seq": pos, "file": name,
                                   "reason": "记录哈希不匹配，内容疑似被篡改"})
                prev_hash = rec.get("hash")
                checked += 1
            elif pos == hi + 1:
                if rec.get("prev") != prev_hash:
                    breaks.append({"seq": pos, "file": name,
                                   "reason": "区间出口与后续记录链接断裂"})
        if stopped:
            break
        if meta:
            if rec_start is None or rec_end is None or pos != meta["end"]:
                breaks.append({"seq": None, "file": name,
                               "reason": "轮转清单区间与分段文件不符"})
            elif meta["first"] != rec_start.get("hash") or meta["last"] != rec_end.get("hash"):
                breaks.append({"seq": None, "file": name,
                               "reason": "轮转清单首尾哈希与分段文件不符"})
    return checked


def export_report(result, report_path):
    """把 verify 结果导出为 JSON 对账报告。返回 report_path。"""
    report = {
        "tool": "auditchain",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": result["ok"],
        "chain_length": result["chain_length"],
        "range": result["range"],
        "checked": result["checked"],
        "breaks": result["breaks"],
    }
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report_path
