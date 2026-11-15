"""数据文件清单：记录大小、校验和、分片号，原子写，可校验、可修复。"""

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone

_CHUNK = 1024 * 1024


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path):
    """读取清单；文件不存在或损坏时返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        return None
    return data


def _dump_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _current_version(path):
    doc = _load(path) or _load(path + ".bak")
    return int(doc.get("version", 0)) if doc else 0


def _entries(manifest):
    """把 files 规范化为 {name, size, checksum, shard}，并列出磁盘上缺失的文件。"""
    data_dir = manifest.get("data_dir") or os.path.dirname(
        os.path.abspath(manifest.get("path", "manifest.json")))
    default_shard = manifest.get("shard", 0)
    entries, missing = [], []
    for item in manifest.get("files", []):
        if isinstance(item, str):
            name, shard = item, default_shard
        else:
            name, shard = item["name"], item.get("shard", default_shard)
        full = os.path.join(data_dir, name)
        if not os.path.isfile(full):
            missing.append(name)
            continue
        entries.append({
            "name": name,
            "size": os.path.getsize(full),
            "checksum": _sha256(full),
            "shard": shard,
        })
    return data_dir, entries, missing


def write(manifest):
    """原子写清单：先写中间文件再替换；新清单自身校验失败时不覆盖原清单。

    版本号只增不减；替换前把上一版留作 .bak，写完自检损坏则回退。
    """
    path = manifest.get("path", "manifest.json")
    data_dir, entries, missing = _entries(manifest)
    if missing:
        return {"path": path, "ok": False, "version": _current_version(path),
                "files": 0, "checksum": None,
                "error": "files missing: " + ", ".join(missing)}
    doc = {
        "version": _current_version(path) + 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": entries,
    }
    doc["checksum"] = hashlib.sha256(
        json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    _dump_json(path, doc)
    if _load(path) is None:  # 写完自检，损坏则回退上一版
        rollback(path)
        return {"path": path, "ok": False, "version": _current_version(path),
                "files": 0, "checksum": None,
                "error": "self-check failed, rolled back"}
    return {"path": path, "ok": True, "version": doc["version"],
            "files": len(entries), "checksum": doc["checksum"]}


def rollback(path):
    """清单损坏时回退到上一版（.bak）。"""
    bak = path + ".bak"
    if not os.path.exists(bak):
        return {"path": path, "ok": False, "version": None, "error": "no backup"}
    os.replace(bak, path)
    doc = _load(path)
    return {"path": path, "ok": doc is not None,
            "version": doc.get("version") if doc else None}


def _is_sidecar(manifest_path, full):
    manifest_full = os.path.abspath(manifest_path)
    full = os.path.abspath(full)
    return full == manifest_full or full.startswith(manifest_full + ".")


def verify(path, data_dir=None, shard=None):
    """校验清单，列出缺失、多余和损坏；shard 指定时只校验一个分片（试运行）。"""
    doc = _load(path)
    if doc is None:
        return {"path": path, "ok": False, "version": None, "missing": [],
                "extra": [], "corrupt": [], "checksum": None, "shard": shard,
                "error": "manifest missing or corrupt"}
    data_dir = data_dir or os.path.dirname(os.path.abspath(path))
    entries = [e for e in doc["files"] if shard is None or e.get("shard") == shard]
    missing, corrupt, listed = [], [], set()
    for entry in entries:
        name = entry["name"]
        listed.add(name)
        full = os.path.join(data_dir, name)
        if not os.path.isfile(full):
            missing.append(name)
        elif (os.path.getsize(full) != entry.get("size")
                or _sha256(full) != entry.get("checksum")):
            corrupt.append(name)
    extra = []
    if shard is None and os.path.isdir(data_dir):  # 多余文件只在全量校验时统计
        for name in sorted(os.listdir(data_dir)):
            full = os.path.join(data_dir, name)
            if (os.path.isfile(full) and name not in listed
                    and not _is_sidecar(path, full)):
                extra.append(name)
    ok = not (missing or corrupt or extra)
    return {"path": path, "ok": ok, "version": doc.get("version"),
            "missing": missing, "extra": extra, "corrupt": corrupt,
            "checksum": doc.get("checksum"), "shard": shard}


def diff(path, data_dir=None, shard=None):
    """差异清单：缺失、多余、损坏。"""
    result = verify(path, data_dir=data_dir, shard=shard)
    return {"version": result["version"], "shard": shard,
            "missing": result["missing"], "extra": result["extra"],
            "corrupt": result["corrupt"]}


def export_diff(path, out, data_dir=None, shard=None):
    """导出差异清单为 JSON 文件。"""
    d = diff(path, data_dir=data_dir, shard=shard)
    _dump_json(out, d)
    return dict(d, out=out)


def _log_dir(path):
    return path + ".repairs"


def _append_repair_log(path, record):
    os.makedirs(_log_dir(path), exist_ok=True)
    log_file = os.path.join(_log_dir(path), record["day"] + ".jsonl")
    with open(log_file, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def repair(path, source_dir, data_dir=None, shard=None):
    """修复只补缺失项：从 source_dir 拷回缺失文件，多余和损坏不擅自处理。

    每次修复按天记录日志；返回清单版本、校验结果和修复清单。
    """
    result = verify(path, data_dir=data_dir, shard=shard)
    repaired, failed = [], []
    doc = _load(path)
    if doc is not None:
        data_dir = data_dir or os.path.dirname(os.path.abspath(path))
        entries = {e["name"]: e for e in doc["files"]}
        for name in result["missing"]:
            src = os.path.join(source_dir, name)
            dst = os.path.join(data_dir, name)
            if not os.path.isfile(src):
                failed.append(name)
                continue
            tmp = dst + ".tmp"
            shutil.copy2(src, tmp)
            if _sha256(tmp) != entries[name].get("checksum"):
                os.remove(tmp)
                failed.append(name)
                continue
            os.replace(tmp, dst)
            repaired.append(name)
    if repaired or failed:
        now = datetime.now(timezone.utc)
        _append_repair_log(path, {
            "time": now.isoformat(), "day": now.strftime("%Y-%m-%d"),
            "version": result["version"], "shard": shard,
            "repaired": repaired, "failed": failed,
        })
    return {"path": path, "ok": not failed and doc is not None,
            "version": result["version"], "verify": result,
            "repaired": repaired, "failed": failed}


def repair_log(path, day=None):
    """按天汇总的修复记录；day='YYYY-MM-DD' 时只看那一天，值班可查每次修复的文件清单。"""
    log_dir = _log_dir(path)
    if day:
        days = [day]
    elif os.path.isdir(log_dir):
        days = sorted(f[:-len(".jsonl")] for f in os.listdir(log_dir)
                      if f.endswith(".jsonl"))
    else:
        days = []
    summary = {}
    for d in days:
        log_file = os.path.join(log_dir, d + ".jsonl")
        if not os.path.isfile(log_file):
            continue
        with open(log_file, "r", encoding="utf-8") as handle:
            summary[d] = [json.loads(line) for line in handle if line.strip()]
    return summary


def export_repairs(path, out, day=None):
    """导出修复记录（可按天）。"""
    logs = repair_log(path, day=day)
    _dump_json(out, logs)
    return {"out": out, "days": sorted(logs)}


def export_manifest(path, out, shard=None):
    """按分片导出清单查看；shard 为空导出全量。"""
    doc = _load(path)
    if doc is None:
        return {"out": None, "ok": False, "error": "manifest missing or corrupt"}
    files = [e for e in doc["files"] if shard is None or e.get("shard") == shard]
    _dump_json(out, {"version": doc["version"], "shard": shard,
                     "checksum": doc.get("checksum"), "files": files})
    return {"out": out, "ok": True, "version": doc["version"], "files": len(files)}
