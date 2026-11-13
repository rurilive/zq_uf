"""按大小和时间策略合并目录里的小文件。"""
import hashlib
import os
import tempfile
import time

DEFAULT_POLICY = {
    "max_file_size": 1 << 20,
    "max_batch_size": 8 << 20,
    "min_age_seconds": 0,
}

_OUTPUT_PREFIX = "merged"
_TEMP_PREFIX = ".compact-"


def _resolve_policy(policy):
    resolved = dict(DEFAULT_POLICY)
    if policy:
        resolved.update(policy)
    return resolved


def _stats(lines):
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return len(lines), digest.hexdigest()


def _is_output(name):
    return name == _OUTPUT_PREFIX + ".txt" or (
        name.startswith(_OUTPUT_PREFIX + "-") and name.endswith(".txt")
    )


def _snapshot(root, policy, now):
    manifest = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        if name.startswith(_TEMP_PREFIX) or _is_output(name):
            continue
        stat = os.stat(path)
        if stat.st_size >= policy["max_file_size"]:
            continue
        if now - stat.st_mtime < policy["min_age_seconds"]:
            continue
        manifest.append((name, stat.st_size))
    return manifest


def _batches(manifest, max_batch_size):
    batches, current, total = [], [], 0
    for name, size in manifest:
        if current and total + size > max_batch_size:
            batches.append(current)
            current, total = [], 0
        current.append(name)
        total += size
    if current or not batches:
        batches.append(current)
    return batches


def _merge_batch(root, names, out_path):
    lines = []
    for name in names:
        with open(os.path.join(root, name), encoding="utf-8") as handle:
            lines.extend(handle.read().splitlines())
    expected = _stats(lines)
    fd, tmp_path = tempfile.mkstemp(dir=root, prefix=_TEMP_PREFIX)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        with open(tmp_path, encoding="utf-8") as handle:
            actual = _stats(handle.read().splitlines())
        if actual != expected:
            raise RuntimeError("compact 校验失败: 行数或校验和不一致")
        os.replace(tmp_path, out_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    for name in names:
        try:
            os.remove(os.path.join(root, name))
        except FileNotFoundError:
            pass


def compact(root, policy=None):
    policy = _resolve_policy(policy)
    manifest = _snapshot(root, policy, time.time())
    outputs = []
    for index, batch in enumerate(_batches(manifest, policy["max_batch_size"])):
        name = "merged.txt" if index == 0 else "merged-%d.txt" % (index + 1)
        out_path = os.path.join(root, name)
        if not batch and os.path.exists(out_path):
            outputs.append(out_path)
            continue
        _merge_batch(root, batch, out_path)
        outputs.append(out_path)
    if len(outputs) == 1:
        return outputs[0]
    return outputs
