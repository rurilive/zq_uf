"""分块校验、坏块修复、分区清单与审计。

纯标准库。状态目录默认 ``./.lakechecksum``，可用环境变量
``LAKECHECKSUM_HOME`` 覆盖；块大小默认 4 MiB，可用
``LAKECHECKSUM_BLOCK_SIZE``（字节）覆盖。
"""
import hashlib
import json
import os
import time

_DEFAULT_BLOCK_SIZE = 4 * 1024 * 1024


def _state_dir():
    return os.environ.get("LAKECHECKSUM_HOME", os.path.join(os.getcwd(), ".lakechecksum"))


def _block_size():
    raw = os.environ.get("LAKECHECKSUM_BLOCK_SIZE")
    return max(1, int(raw)) if raw else _DEFAULT_BLOCK_SIZE


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _cache_path(abspath):
    key = hashlib.sha256(abspath.encode("utf-8")).hexdigest()
    return os.path.join(_ensure_dir(os.path.join(_state_dir(), "files")), key + ".json")


def _partition_dir(partition):
    return os.path.join(_state_dir(), "partitions", partition)


def _manifest_path(partition):
    return os.path.join(_partition_dir(partition), "manifest.json")


def _audit_path(partition):
    return os.path.join(_partition_dir(partition), "audit.jsonl")


def _load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path, payload):
    _ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _audit(partition, entry):
    entry = dict(entry, ts=time.time(), partition=partition)
    _ensure_dir(os.path.dirname(_audit_path(partition)))
    with open(_audit_path(partition), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _read_blocks(path, block_size):
    """按固定块读取文件，返回 (块列表, 整文件 sha256)。"""
    digest = hashlib.sha256()
    blocks = []
    offset = 0
    with open(path, "rb") as handle:
        while True:
            data = handle.read(block_size)
            if not data:
                break
            digest.update(data)
            blocks.append({
                "offset": offset,
                "length": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
            offset += len(data)
    return blocks, digest.hexdigest()


def verify(path):
    """分块校验。文件 mtime+size 未变时直接复用缓存，只重算变化块。

    返回整文件 sha256、块数、变化块下标和每块状态。
    """
    abspath = os.path.abspath(path)
    block_size = _block_size()
    stat = os.stat(abspath)
    cache_file = _cache_path(abspath)
    cache = _load_json(cache_file)
    if (cache and cache["size"] == stat.st_size
            and cache["mtime"] == stat.st_mtime
            and cache["block_size"] == block_size):
        blocks = cache["blocks"]
        return {
            "sha256": cache["sha256"],
            "blocks": len(blocks),
            "block_size": block_size,
            "changed": [],
            "recomputed": 0,
            "block_status": ["ok"] * len(blocks),
            "cached": True,
        }

    old = cache["blocks"] if cache and cache.get("block_size") == block_size else []
    blocks, digest = _read_blocks(abspath, block_size)
    status, changed = [], []
    for index, block in enumerate(blocks):
        if index >= len(old):
            state = "new"
        elif old[index]["sha256"] != block["sha256"]:
            state = "changed"
        else:
            state = "ok"
        status.append(state)
        if state != "ok":
            changed.append(index)
    _save_json(cache_file, {
        "path": abspath,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "block_size": block_size,
        "sha256": digest,
        "blocks": blocks,
    })
    return {
        "sha256": digest,
        "blocks": len(blocks),
        "block_size": block_size,
        "changed": changed,
        "recomputed": len(blocks),
        "block_status": status,
        "cached": False,
    }


def track(partition, path, replica=None):
    """把文件纳入分区清单，记录块哈希与副本位置，返回清单差异。"""
    abspath = os.path.abspath(path)
    verify(abspath)
    cache = _load_json(_cache_path(abspath))
    manifest = _load_json(_manifest_path(partition)) or {
        "partition": partition,
        "files": {},
    }
    kind = "updated" if abspath in manifest["files"] else "added"
    manifest["files"][abspath] = {
        "size": cache["size"],
        "mtime": cache["mtime"],
        "block_size": cache["block_size"],
        "sha256": cache["sha256"],
        "replica": os.path.abspath(replica) if replica else None,
        "blocks": cache["blocks"],
    }
    _save_json(_manifest_path(partition), manifest)
    _audit(partition, {
        "action": "track",
        "path": abspath,
        "before_sha256": None,
        "after_sha256": cache["sha256"],
    })
    return {"manifest_diff": {kind: [abspath]}, "blocks": len(cache["blocks"])}


def _repair_file(entry, path):
    """修复单个文件。

    返回 (修复块数, 剩余坏块下标, 修复前实际 sha256 或 None, 修复后 sha256 或 None)。
    """
    expected = entry["blocks"]
    block_size = entry["block_size"]
    replica = entry.get("replica")
    if not os.path.exists(path):
        if replica and os.path.exists(replica):
            with open(replica, "rb") as src, open(path, "wb") as dst:
                dst.write(src.read())
            _, digest = _read_blocks(path, block_size)
            if digest == entry["sha256"]:
                return len(expected), [], None, digest
        return 0, list(range(len(expected))), None, None

    blocks, before_digest = _read_blocks(path, block_size)
    bad = [i for i, exp in enumerate(expected)
           if i >= len(blocks) or blocks[i]["sha256"] != exp["sha256"]]
    bad.extend(range(len(expected), len(blocks)))  # 文件变长，尾部视为坏块
    if not bad:
        return 0, [], before_digest, entry["sha256"]

    fixed, still = [], []
    if replica and os.path.exists(replica):
        with open(replica, "rb") as src, open(path, "r+b") as dst:
            for index in bad:
                if index >= len(expected):
                    continue  # 尾部多余块，截断处理
                exp = expected[index]
                src.seek(exp["offset"])
                data = src.read(exp["length"])
                if (len(data) == exp["length"]
                        and hashlib.sha256(data).hexdigest() == exp["sha256"]):
                    dst.seek(exp["offset"])
                    dst.write(data)
                    fixed.append(index)
                else:
                    still.append(index)
            if not still:
                dst.truncate(sum(b["length"] for b in expected))
    else:
        still = [i for i in bad if i < len(expected)]

    if len(expected) == 0 and not fixed:
        return 0, still, before_digest, entry["sha256"]
    _, digest = _read_blocks(path, block_size)
    if digest == entry["sha256"]:
        still = []
    return len(fixed), still, before_digest, digest


def repair(partition):
    """修复分区：坏块从副本重取，修复后更新清单并写审计。

    返回修复块数、剩余坏块、清单差异。
    """
    empty = {"repaired": 0, "remaining_bad": 0, "bad_blocks": [],
             "manifest_diff": {"updated": [], "removed": []}}
    manifest = _load_json(_manifest_path(partition))
    if manifest is None:
        return empty

    repaired = 0
    bad_blocks = []
    updated = []
    for path, entry in sorted(manifest["files"].items()):
        count, still, before_sha, after_sha = _repair_file(entry, path)
        if count == 0 and not still:
            continue
        repaired += count
        bad_blocks.extend({"path": path, "block": i} for i in still)
        if after_sha is not None and os.path.exists(path):
            stat = os.stat(path)
            blocks, digest = _read_blocks(path, entry["block_size"])
            entry.update(size=stat.st_size, mtime=stat.st_mtime,
                         sha256=digest, blocks=blocks)
            after_sha = digest
            updated.append(path)
            _save_json(_cache_path(path), {  # 同步 verify 缓存
                "path": path,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "block_size": entry["block_size"],
                "sha256": digest,
                "blocks": blocks,
            })
        _audit(partition, {
            "action": "repair",
            "path": path,
            "before_sha256": before_sha,
            "after_sha256": after_sha,
            "repaired_blocks": count,
            "remaining_blocks": list(still),
        })
    _save_json(_manifest_path(partition), manifest)
    return {
        "repaired": repaired,
        "remaining_bad": len(bad_blocks),
        "bad_blocks": bad_blocks,
        "manifest_diff": {"updated": updated, "removed": []},
    }


def drop_partition(partition):
    """删除分区并同步清理清单，审计保留可查。返回被清理的文件列表。"""
    manifest = _load_json(_manifest_path(partition))
    removed = sorted(manifest["files"]) if manifest else []
    if os.path.exists(_manifest_path(partition)):
        os.remove(_manifest_path(partition))
    _audit(partition, {"action": "drop_partition", "removed": removed,
                       "before_sha256": None, "after_sha256": None})
    return {"removed": removed}


def audit(partition=None):
    """按分区查询审计记录；partition 为 None 时返回所有分区。"""
    base = os.path.join(_state_dir(), "partitions")
    if not os.path.isdir(base):
        return []
    names = [partition] if partition else sorted(os.listdir(base))
    entries = []
    for name in names:
        path = _audit_path(name)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    return entries
