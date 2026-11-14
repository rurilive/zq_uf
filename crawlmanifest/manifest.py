"""分片采集清单：每分片一份清单，原子写，校验只读，恢复只补差异。

目录布局（root 为任务目录）：

    root/
        manifests/manifest-shard-0000.json   # 清单，每分片一份
        data/shard-0000.jsonl                # 产物，与清单分目录存放
"""
import hashlib
import json
import os
import re
import tempfile

MANIFEST_DIR = "manifests"
DATA_DIR = "data"
_NAME_RE = re.compile(r"^manifest-shard-(\d+)\.json$")


def _shard_name(shard):
    return "manifest-shard-%04d.json" % shard


def _shard_of(path):
    match = _NAME_RE.match(os.path.basename(path))
    return int(match.group(1)) if match else None


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_rows(path):
    rows = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            rows += chunk.count(b"\n")
    return rows


def _resolve(root, artifact):
    if os.path.isabs(artifact):
        return artifact
    return os.path.join(root, artifact)


def write(manifest, root="."):
    """写入一个分片的清单，返回清单路径。

    记录分片号、行数、校验和、状态；先写同目录临时文件再原子改名，
    崩溃不会留下写一半的清单；不同分片落在不同文件，并发写互不影响。
    """
    shard = int(manifest.get("shard", 0))
    record = dict(manifest)
    record["shard"] = shard
    record.setdefault("rows", 0)
    record.setdefault("checksum", None)
    record.setdefault("status", "done")
    record.setdefault("artifact", None)

    artifact = record["artifact"]
    if artifact:
        full = _resolve(root, artifact)
        if os.path.exists(full):
            if record["checksum"] is None:
                record["checksum"] = _sha256(full)
            if not record["rows"]:
                record["rows"] = _count_rows(full)

    mdir = os.path.join(root, MANIFEST_DIR)
    os.makedirs(mdir, exist_ok=True)
    path = os.path.join(mdir, _shard_name(shard))
    fd, tmp = tempfile.mkstemp(
        prefix=".tmp-%04d-" % shard, suffix=".json", dir=mdir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def _locate(path):
    """从给定路径推出 (root, 清单文件列表)。"""
    if os.path.isdir(path):
        if os.path.isdir(os.path.join(path, MANIFEST_DIR)):
            root, mdir = path, os.path.join(path, MANIFEST_DIR)
        elif os.path.basename(os.path.normpath(path)) == MANIFEST_DIR:
            root, mdir = os.path.dirname(os.path.normpath(path)), path
        else:
            root = mdir = path
        files = [
            os.path.join(mdir, name)
            for name in sorted(os.listdir(mdir))
            if _NAME_RE.match(name) or name.endswith(".json")
        ]
        return root, files
    mdir = os.path.dirname(os.path.abspath(path))
    root = os.path.dirname(mdir) if os.path.basename(mdir) == MANIFEST_DIR else mdir
    return root, [path]


def verify(path):
    """对比清单和产物，只读不改写。

    返回 {"ok", "missing", "extra", "corrupt"}：
    missing 清单有记录但产物缺失；extra 产物存在但无清单记录；
    corrupt 清单无法解析或产物校验和不符。
    """
    root, files = _locate(path)
    missing, extra, corrupt = [], [], []
    referenced = set()
    for name in files:
        try:
            with open(name, encoding="utf-8") as handle:
                record = json.load(handle)
        except Exception:
            corrupt.append(name)
            continue
        artifact = record.get("artifact")
        if not artifact:
            continue
        full = os.path.normpath(_resolve(root, artifact))
        referenced.add(full)
        if not os.path.exists(full):
            missing.append(full)
        elif record.get("checksum") and _sha256(full) != record["checksum"]:
            corrupt.append(full)
    data_dir = os.path.join(root, DATA_DIR)
    if os.path.isdir(data_dir):
        for dirpath, _, filenames in os.walk(data_dir):
            for filename in filenames:
                full = os.path.normpath(os.path.join(dirpath, filename))
                if full not in referenced:
                    extra.append(full)
    ok = not (missing or extra or corrupt)
    return {"ok": ok, "missing": missing, "extra": extra, "corrupt": corrupt}


def recover(root, fetch):
    """先跑一次校验，再只对缺失/损坏的分片调用 fetch(shard) 补差异。

    fetch(shard) 负责重新生成该分片产物，可返回 dict 补充清单字段
    （如 {"artifact": "data/shard-0001.jsonl"}）；行数和校验和会自动重算。
    返回 {"repaired": [...], "report": 复验结果}。
    """
    report = verify(root)
    if report["ok"]:
        return {"repaired": [], "report": report}
    bad = set(report["missing"]) | set(report["corrupt"])
    mdir = os.path.join(root, MANIFEST_DIR)
    shards = set()
    if os.path.isdir(mdir):
        for name in sorted(os.listdir(mdir)):
            shard = _shard_of(name)
            if shard is None:
                continue
            full = os.path.join(mdir, name)
            if full in bad or os.path.normpath(full) in bad:
                shards.add(shard)  # 清单本身损坏
                continue
            try:
                with open(full, encoding="utf-8") as handle:
                    record = json.load(handle)
            except Exception:
                shards.add(shard)
                continue
            artifact = record.get("artifact")
            if artifact and os.path.normpath(_resolve(root, artifact)) in bad:
                shards.add(shard)  # 产物缺失或损坏
    repaired = []
    for shard in sorted(shards):
        result = fetch(shard) or {}
        record = {"shard": shard, "status": "done"}
        record.update(result)
        write(record, root)
        repaired.append(shard)
    return {"repaired": repaired, "report": verify(root)}
