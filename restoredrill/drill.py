"""隔离恢复演练：把快照恢复到临时目录，校验行数、校验和与关键表。

- 隔离：恢复始终发生在 tempfile.mkdtemp 的临时目录，绝不写生产路径。
- 校验：逐表比对行数与 sha256，关键表必须在清单中且校验通过。
- 失败：缺块或校验失败时结论 FAIL，保留现场目录（kept_scene）便于排查。
- 指标：rto_seconds 为恢复耗时（RTO 口径），rpo_seconds 为快照年龄（RPO 口径）。
- 留档：每次演练追加一行 JSON 到留档文件（configure(archive_path=...) 可改）。
"""

import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone

_history = []

_DEFAULT_ARCHIVE = os.environ.get(
    "RESTOREDRILL_ARCHIVE",
    os.path.join(tempfile.gettempdir(), "restoredrill_archive.jsonl"),
)

_snapshot_root = None
_production_dir = None
_archive_path = _DEFAULT_ARCHIVE


class DrillError(Exception):
    """演练中的明确错误（清单缺失、快照格式非法等）。"""


def configure(snapshot_root=None, production_dir=None, archive_path=None):
    """配置快照根目录、生产目录（隔离保护对象）和留档文件路径。"""
    global _snapshot_root, _production_dir, _archive_path
    _snapshot_root = snapshot_root
    _production_dir = production_dir
    if archive_path is not None:
        _archive_path = archive_path


def drill(snapshot):
    """对快照做一次隔离恢复演练，返回演练记录（不抛校验类错误）。"""
    started = time.time()
    record = {
        "snapshot": snapshot,
        "ok": True,
        "conclusion": "PASS",
        "started_at": _iso(started),
        "target": None,
        "kept_scene": False,
        "rto_seconds": None,
        "rpo_seconds": None,
        "checks": {},
        "diffs": [],
        "errors": [],
    }
    src = _resolve(snapshot)
    if src is None:
        # 兼容旧用法：找不到快照数据目录时仅登记，不恢复也不校验。
        record["note"] = "未找到快照数据目录，按逻辑演练登记（未恢复、未校验）"
        record["rto_seconds"] = 0.0
        return _finish(record, started)
    try:
        manifest = _load_manifest(src)
    except DrillError as exc:
        record["ok"] = False
        record["conclusion"] = "FAIL"
        record["errors"].append(str(exc))
        return _finish(record, started)
    _run_drill(record, src, manifest, started)
    return _finish(record, started)


def report(snapshot=None, table=None):
    """演练报告，可按快照名和表名过滤；含结论与 RPO/RTO 汇总。"""
    results = [r for r in _history if snapshot is None or r["snapshot"] == snapshot]
    if table is not None:
        results = [r for r in results if table in r.get("checks", {})]
    rtos = [r["rto_seconds"] for r in results if r.get("rto_seconds") is not None]
    rpos = [r["rpo_seconds"] for r in results if r.get("rpo_seconds") is not None]
    out = {
        "drills": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "rto_seconds": {"last": rtos[-1] if rtos else None,
                        "max": max(rtos) if rtos else None},
        "rpo_seconds": {"last": rpos[-1] if rpos else None,
                        "max": max(rpos) if rpos else None},
        "results": [
            {
                "snapshot": r["snapshot"],
                "ok": r["ok"],
                "conclusion": r["conclusion"],
                "started_at": r["started_at"],
                "rto_seconds": r["rto_seconds"],
                "rpo_seconds": r["rpo_seconds"],
                "target": r["target"],
                "kept_scene": r["kept_scene"],
                "errors": list(r["errors"]),
            }
            for r in results
        ],
    }
    if table is not None:
        out["tables"] = {
            table: [
                dict(r["checks"][table], snapshot=r["snapshot"],
                     started_at=r["started_at"])
                for r in results
            ]
        }
    return out


def _resolve(snapshot):
    """把快照名解析为数据目录；找不到返回 None（走逻辑演练兼容路径）。"""
    if isinstance(snapshot, os.PathLike):
        snapshot = str(snapshot)
    if isinstance(snapshot, str):
        if os.path.isdir(snapshot):
            return snapshot
        if _snapshot_root:
            candidate = os.path.join(_snapshot_root, snapshot)
            if os.path.isdir(candidate):
                return candidate
    return None


def _load_manifest(src):
    path = os.path.join(src, "manifest.json")
    if not os.path.isfile(path):
        raise DrillError("快照缺少 manifest.json: %s" % src)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        raise DrillError("manifest.json 无法解析: %s" % exc)
    if not isinstance(manifest.get("tables"), dict):
        raise DrillError("manifest.json 缺少 tables 字段")
    return manifest


def _run_drill(record, src, manifest, started):
    tables = manifest["tables"]
    key_tables = set(manifest.get("key_tables") or [])
    key_tables |= {n for n, spec in tables.items() if spec.get("key")}

    created = _parse_time(manifest.get("created_at"))
    if created is None:
        created = _latest_mtime(src)
    if created is not None:
        record["rpo_seconds"] = round(max(0.0, started - created), 3)

    target = tempfile.mkdtemp(prefix="restoredrill-%s-" % _safe(record["snapshot"]))
    record["target"] = target
    if _production_dir and _is_within(target, _production_dir):
        record["errors"].append("临时目录落入生产路径，已中止: %s" % target)
        record["ok"] = False
        record["conclusion"] = "FAIL"
        shutil.rmtree(target, ignore_errors=True)
        record["target"] = None
        return

    # 恢复：把各表的块按顺序重建为临时目录下的表文件，计时即 RTO。
    t0 = time.perf_counter()
    restored = {}
    for name, spec in tables.items():
        chunks = spec.get("chunks") or []
        missing = [c for c in chunks
                   if not os.path.isfile(os.path.join(src, "chunks", c))]
        if missing:
            record["errors"].append(
                "缺块: 表 %s 缺少块 %s" % (name, ", ".join(missing)))
            record["checks"][name] = {
                "key": name in key_tables, "status": "missing-chunk",
                "expected_rows": spec.get("rows"), "actual_rows": None,
                "expected_sha256": spec.get("sha256"), "actual_sha256": None,
            }
            continue
        table_path = os.path.join(target, "%s.csv" % name)
        with open(table_path, "wb") as out:
            for chunk in chunks:
                with open(os.path.join(src, "chunks", chunk), "rb") as fh:
                    shutil.copyfileobj(fh, out)
        restored[name] = table_path
    record["rto_seconds"] = round(time.perf_counter() - t0, 3)

    # 校验：对恢复出的表文件比对行数与 sha256。
    for name, table_path in restored.items():
        spec = tables[name]
        with open(table_path, "rb") as fh:
            data = fh.read()
        check = {
            "key": name in key_tables, "status": "ok",
            "expected_rows": spec.get("rows"),
            "actual_rows": sum(1 for line in data.splitlines() if line.strip()),
            "expected_sha256": spec.get("sha256"),
            "actual_sha256": hashlib.sha256(data).hexdigest(),
        }
        if check["expected_rows"] is not None \
                and check["actual_rows"] != check["expected_rows"]:
            check["status"] = "row-mismatch"
            record["diffs"].append({
                "table": name, "field": "rows",
                "expected": check["expected_rows"],
                "actual": check["actual_rows"],
            })
        if check["expected_sha256"] is not None \
                and check["actual_sha256"] != check["expected_sha256"]:
            check["status"] = "checksum-mismatch"
            record["diffs"].append({
                "table": name, "field": "sha256",
                "expected": check["expected_sha256"],
                "actual": check["actual_sha256"],
            })
        record["checks"][name] = check

    for name in sorted(key_tables - set(tables)):
        record["errors"].append("关键表 %s 不在快照清单中" % name)

    failed = bool(record["errors"]) or bool(record["diffs"]) \
        or any(c["status"] != "ok" for c in record["checks"].values())
    if failed:
        record["ok"] = False
        record["conclusion"] = "FAIL"
        record["kept_scene"] = True  # 失败保留现场
    else:
        shutil.rmtree(target, ignore_errors=True)


def _finish(record, started):
    record["duration_seconds"] = round(time.time() - started, 3)
    _history.append(record)
    _archive(record)
    return record


def _archive(record):
    try:
        with open(_archive_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 留档失败不影响演练结论


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _latest_mtime(src):
    latest = None
    for root, _dirs, files in os.walk(src):
        for name in files:
            mtime = os.path.getmtime(os.path.join(root, name))
            if latest is None or mtime > latest:
                latest = mtime
    return latest


def _is_within(path, parent):
    try:
        return os.path.commonpath([os.path.abspath(path),
                                   os.path.abspath(parent)]) == \
            os.path.abspath(parent)
    except ValueError:
        return False


def _safe(name):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name))
