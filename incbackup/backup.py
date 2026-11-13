"""基于内容哈希分块的增量快照备份。

存储布局（dest 目录）::

    TAGS.json                 标签 -> 清单相对路径
    blocks/xx/<sha256>        内容寻址块（原始字节，按哈希前两位分目录）
    snap-NNNN/SNAPSHOT.json   快照清单（文件块引用、父快照、目录与链接）
    snap-NNNN/tree/...        该快照的物化文件视图（硬链接优先）

块只在内容哈希首次出现时写入，后续备份全部复用；恢复沿父快照链逐段
校验清单与块哈希，链断裂或块损坏一律抛错，不做静默跳过。
"""

import hashlib
import json
import os
import shutil

TAG_FILE = "TAGS.json"
MANIFEST_NAME = "SNAPSHOT.json"
BLOCKS_DIR = "blocks"

# 固定分块阈值：文件按该大小顺序切块，最后一块可以更短；空文件没有块。
CHUNK_SIZE = 64 * 1024


class BackupError(Exception):
    """备份阶段的明确错误（根目录无效、清单损坏等）。"""


class RestoreError(BackupError):
    """恢复阶段的明确错误（未知标签、父链断裂、块缺失或损坏等）。"""


def _blocks_root(dest):
    return os.path.join(dest, BLOCKS_DIR)


def _block_path(dest, digest):
    return os.path.join(_blocks_root(dest), digest[:2], digest)


def _atomic_write_json(path, payload):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, path)


def _load_tags(dest):
    """读取标签索引；文件存在但损坏时抛错，不存在则视为全新仓库。"""
    tag_path = os.path.join(dest, TAG_FILE)
    if not os.path.exists(tag_path):
        return {}
    try:
        with open(tag_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        tags = payload["tags"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError("标签索引损坏，无法继续备份: %s (%s)" % (tag_path, exc))
    if not isinstance(tags, dict):
        raise BackupError("标签索引格式非法: %s" % tag_path)
    return dict(tags)


def _next_tag(tags):
    return "snap-%04d" % (len(tags) + 1)


def _hash_chunks(root, rel_path, dest, counters):
    """流式切块并写入缺失块，返回 (块哈希列表, 文件字节数)。"""
    source = os.path.join(root, rel_path)
    chunks = []
    size = 0
    with open(source, "rb") as handle:
        while True:
            data = handle.read(CHUNK_SIZE)
            if not data:
                break
            size += len(data)
            digest = hashlib.sha256(data).hexdigest()
            chunks.append(digest)
            block_path = _block_path(dest, digest)
            if os.path.exists(block_path):
                counters["reused"] += 1
            else:
                os.makedirs(os.path.dirname(block_path), exist_ok=True)
                tmp_path = block_path + ".tmp"
                try:
                    with open(tmp_path, "wb") as block_handle:
                        block_handle.write(data)
                    os.replace(tmp_path, block_path)
                except OSError:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    raise
                counters["new"] += 1
    return chunks, size


def _collect_entries(root):
    """递归收集目录、普通文件与符号链接；遇到不支持的类型直接报错。"""
    directories = []
    files = []

    def walk(current_rel):
        current_abs = os.path.join(root, current_rel) if current_rel else root
        try:
            entries = sorted(os.scandir(current_abs), key=lambda entry: entry.name)
        except OSError as exc:
            raise BackupError("无法读取目录 %s: %s" % (current_abs, exc))
        for entry in entries:
            rel = os.path.join(current_rel, entry.name) if current_rel else entry.name
            if entry.is_symlink():
                try:
                    target = os.readlink(entry.path)
                except OSError as exc:
                    raise BackupError("无法读取符号链接 %s: %s" % (rel, exc))
                files.append({"path": rel, "symlink": target})
            elif entry.is_dir(follow_symlinks=False):
                directories.append(rel)
                walk(rel)
            elif entry.is_file(follow_symlinks=False):
                files.append({"path": rel, "rel": rel})
            else:
                raise BackupError("不支持的文件类型，已中止备份: %s" % rel)

    walk("")
    return directories, files


def _link_or_copy(source_block, target_file):
    """优先硬链接块以零拷贝物化视图，跨设备/权限受限时退回复制。"""
    try:
        os.link(source_block, target_file)
    except OSError:
        shutil.copyfile(source_block, target_file)


def _materialize(manifest, tree, blocks_root):
    """按清单把文件视图物化到 tree 目录。"""
    for rel_dir in manifest.get("directories", []):
        os.makedirs(os.path.join(tree, rel_dir), exist_ok=True)
    for entry in manifest["files"]:
        target = os.path.join(tree, entry["path"])
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if "symlink" in entry:
            os.symlink(entry["symlink"], target)
            continue
        chunks = entry["chunks"]
        if not chunks:
            open(target, "wb").close()
        elif len(chunks) == 1:
            block_path = os.path.join(blocks_root, chunks[0][:2], chunks[0])
            _link_or_copy(block_path, target)
        else:
            with open(target, "wb") as out:
                for digest in chunks:
                    block_path = os.path.join(blocks_root, digest[:2], digest)
                    with open(block_path, "rb") as block_handle:
                        shutil.copyfileobj(block_handle, out)
        if "mode" in entry:
            os.chmod(target, entry["mode"])


def backup(root, dest="backups"):
    """对 root 做一次内容哈希增量备份，返回标签与块统计。

    返回字典包含：tag、path（快照物化视图目录）、files、new_blocks、
    reused_blocks、parent。
    """
    if not os.path.isdir(root):
        raise BackupError("备份根目录不存在或不是目录: %s" % root)

    os.makedirs(_blocks_root(dest), exist_ok=True)
    tags = _load_tags(dest)
    parent = sorted(tags)[-1] if tags else None
    if parent is not None and not os.path.exists(os.path.join(dest, tags[parent])):
        raise BackupError("父快照清单缺失，拒绝在断裂的链上继续备份: %s" % parent)

    directories, entries = _collect_entries(root)
    counters = {"new": 0, "reused": 0}
    manifest_files = []
    for entry in entries:
        rel = entry["path"]
        record = {"path": rel}
        if "symlink" in entry:
            record["symlink"] = entry["symlink"]
        else:
            chunks, size = _hash_chunks(root, rel, dest, counters)
            record["chunks"] = chunks
            record["size"] = size
            record["mode"] = os.stat(os.path.join(root, rel)).st_mode & 0o777
        manifest_files.append(record)

    tag = _next_tag(tags)
    snap_dir = os.path.join(dest, tag)
    tree = os.path.join(snap_dir, "tree")
    os.makedirs(tree, exist_ok=True)
    manifest = {
        "tag": tag,
        "parent": parent,
        "chunk_size": CHUNK_SIZE,
        "directories": directories,
        "files": manifest_files,
    }
    manifest_path = os.path.join(snap_dir, MANIFEST_NAME)
    _atomic_write_json(manifest_path, manifest)
    _materialize(manifest, tree, _blocks_root(dest))

    tags[tag] = os.path.join(tag, MANIFEST_NAME)
    _atomic_write_json(os.path.join(dest, TAG_FILE), {"version": 1, "tags": tags})

    return {
        "tag": tag,
        "path": tree,
        "files": len(manifest_files),
        "new_blocks": counters["new"],
        "reused_blocks": counters["reused"],
        "parent": parent,
    }


def _read_manifest(source, manifest_rel):
    path = os.path.join(source, manifest_rel)
    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except FileNotFoundError:
        raise RestoreError("快照清单缺失，父链已断裂: %s" % path)
    except (OSError, ValueError) as exc:
        raise RestoreError("快照清单损坏，无法恢复: %s (%s)" % (path, exc))
    required = ("tag", "parent", "files")
    if any(key not in manifest for key in required):
        raise RestoreError("快照清单字段不完整: %s" % path)
    return manifest


def _verify_block(blocks_root, digest):
    block_path = os.path.join(blocks_root, digest[:2], digest)
    if not os.path.isfile(block_path):
        raise RestoreError("快照引用的块缺失: %s" % digest)
    actual = hashlib.sha256()
    try:
        with open(block_path, "rb") as handle:
            for part in iter(lambda: handle.read(CHUNK_SIZE), b""):
                actual.update(part)
    except OSError as exc:
        raise RestoreError("块无法读取: %s (%s)" % (digest, exc))
    if actual.hexdigest() != digest:
        raise RestoreError("块哈希校验失败，内容已损坏: %s" % digest)


def _walk_chain(source, tags, tag):
    """从目标标签沿 parent 走到根，返回（新到旧清单列表，旧到新标签序列）。"""
    chain = []
    order = []
    current_tag = tag
    seen = set()
    while current_tag is not None:
        if current_tag in seen:
            raise RestoreError("父快照链存在环，拒绝恢复: %s" % current_tag)
        seen.add(current_tag)
        if current_tag not in tags:
            raise RestoreError("父快照链断裂，缺少标签: %s" % current_tag)
        manifest = _read_manifest(source, tags[current_tag])
        if manifest["tag"] != current_tag:
            raise RestoreError(
                "清单标签与索引不一致: 索引=%s 清单=%s" % (current_tag, manifest["tag"])
            )
        chain.append(manifest)
        order.append(current_tag)
        current_tag = manifest["parent"]
    return chain, list(reversed(order))


def restore(tag, dest="restore", source="backups"):
    """校验父链与块哈希后，把任意标签的快照恢复到 dest。

    返回字典包含：tag、path、files、chunks_verified、chain（旧到新）。
    链断裂、块缺失/损坏、未知标签均抛 RestoreError，不做部分恢复。
    """
    if not os.path.isdir(source):
        raise RestoreError("备份仓库不存在: %s" % source)
    tag_path = os.path.join(source, TAG_FILE)
    if not os.path.isfile(tag_path):
        raise RestoreError("备份仓库缺少标签索引: %s" % tag_path)
    try:
        with open(tag_path, encoding="utf-8") as handle:
            tags = json.load(handle)["tags"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RestoreError("标签索引损坏，无法恢复: %s (%s)" % (tag_path, exc))
    if tag not in tags:
        known = ", ".join(sorted(tags)) or "(空)"
        raise RestoreError("未知快照标签: %s；已知标签: %s" % (tag, known))

    chain, chain_order = _walk_chain(source, tags, tag)
    blocks_root = os.path.join(source, BLOCKS_DIR)
    chunks_verified = 0
    for manifest in chain:
        for entry in manifest["files"]:
            for digest in entry.get("chunks", []):
                _verify_block(blocks_root, digest)
                chunks_verified += 1

    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)
    target_manifest = chain[0]
    _materialize(target_manifest, dest, blocks_root)

    return {
        "tag": tag,
        "path": dest,
        "files": len(target_manifest["files"]),
        "chunks_verified": chunks_verified,
        "chain": chain_order,
    }
