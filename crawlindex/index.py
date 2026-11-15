"""采集索引：可校验重建、原子切换、版本合并、断点续跑。

纯标准库实现。数据目录内的磁盘布局：
  index.json              当前索引内容
  meta.json               索引元数据（版本号、条目数、更新时间）
  rebuild_checkpoint.json 重建断点（中断后可续跑）
  rollback.log            回退动作日志（JSON 行）
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
import time

logger = logging.getLogger("crawlindex")

_DEFAULT_DIR = os.environ.get("CRAWLINDEX_DIR") or os.path.join(
    os.getcwd(), ".crawlindex")

_lock = threading.RLock()
_data_dir = _DEFAULT_DIR
_current = {}      # key -> {"value": ..., "version": int}，正在服务的索引
_version = 0       # 全局版本号，单调递增
_loaded = False    # 是否已从磁盘加载
_rebuild = None    # 进行中的重建进度 dict，空闲为 None
_pending = {}      # 重建期间的增量更新，切换时按版本合并
_checkpoint_every = 200


class _Interrupt(Exception):
    """重建被中断（should_stop 或 KeyboardInterrupt）。"""


def _paths():
    return {
        "index": os.path.join(_data_dir, "index.json"),
        "meta": os.path.join(_data_dir, "meta.json"),
        "checkpoint": os.path.join(_data_dir, "rebuild_checkpoint.json"),
        "rollback": os.path.join(_data_dir, "rollback.log"),
    }


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_atomic(path, obj):
    """先写临时文件再 os.replace，保证落盘是原子的。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _persist_locked():
    """把当前索引与元数据落盘（调用方需持有 _lock）。"""
    _write_atomic(_paths()["index"], {"entries": _current})
    _write_atomic(_paths()["meta"], {
        "version": _version,
        "entry_count": len(_current),
        "updated_at": time.time(),
    })


def _rollback(reason):
    """回退到旧索引：保留内存中的 _current 不动，动作写日志。"""
    logger.warning("重建失败，回退到旧索引: %s", reason)
    try:
        os.makedirs(_data_dir, exist_ok=True)
        with open(_paths()["rollback"], "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(), "reason": reason, "version": _version,
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _write_ckpt(fingerprint, entries, processed, total):
    try:
        _write_atomic(_paths()["checkpoint"], {
            "fingerprint": fingerprint,
            "entries": entries,
            "processed": processed,
            "total": total,
            "ts": time.time(),
        })
    except OSError:
        logger.warning("重建断点写入失败", exc_info=True)


def _discard_ckpt():
    try:
        os.unlink(_paths()["checkpoint"])
    except OSError:
        pass


def _ensure_loaded():
    """首次访问时从磁盘加载索引；文件损坏或不一致则忽略并告警。"""
    global _loaded, _current, _version
    with _lock:
        if _loaded:
            return
        _loaded = True
        meta = _read_json(_paths()["meta"])
        idx = _read_json(_paths()["index"])
        entries = idx.get("entries") if isinstance(idx, dict) else None
        if (isinstance(meta, dict) and isinstance(entries, dict)
                and meta.get("entry_count") == len(entries)):
            _current = entries
            _version = max(_version, int(meta.get("version", 0)))
        elif meta is not None or idx is not None:
            logger.warning("索引文件损坏或元数据不一致，忽略落盘数据")


def configure(data_dir=None, checkpoint_every=None):
    """配置数据目录与断点写入间隔。"""
    global _data_dir, _loaded, _checkpoint_every
    with _lock:
        if data_dir is not None:
            _data_dir = data_dir
            _loaded = False
        if checkpoint_every:
            _checkpoint_every = int(checkpoint_every)


def reset(data_dir=None):
    """清空内存状态（测试/演示用）；data_dir 缺省恢复默认目录。"""
    global _data_dir, _current, _pending, _rebuild, _version, _loaded
    with _lock:
        _current = {}
        _pending = {}
        _rebuild = None
        _version = 0
        _data_dir = data_dir or _DEFAULT_DIR
        _loaded = False


def version():
    """当前全局版本号（单调递增）。"""
    _ensure_loaded()
    return _version


def update(key, value):
    """增量更新：版本号单调递增；重建期间的更新在切换时按版本合并。"""
    global _version
    _ensure_loaded()
    with _lock:
        _version += 1
        ent = {"value": value, "version": _version}
        _current[key] = ent
        if _rebuild is not None and _rebuild.get("state") == "rebuilding":
            _pending[key] = ent
        try:
            _persist_locked()
        except OSError:
            logger.warning("增量更新落盘失败", exc_info=True)
        return {"key": key, "updated": True, "version": _version}


def query(key):
    """按精确键查索引。读路径不加锁，重建期间查询延迟不上升。"""
    _ensure_loaded()
    ent = _current.get(key)
    return {
        "key": key,
        "found": ent is not None,
        "value": None if ent is None else ent["value"],
        "index_version": _version,
    }


def progress():
    """查看重建进度：state 为 idle / rebuilding / interrupted。"""
    _ensure_loaded()
    with _lock:
        if _rebuild is not None:
            return dict(_rebuild, version=_version)
        ckpt = _read_json(_paths()["checkpoint"])
        if ckpt:
            return {
                "state": "interrupted",
                "processed": ckpt.get("processed", 0),
                "total": ckpt.get("total", 0),
                "resumable": True,
                "version": _version,
            }
        return {"state": "idle", "processed": 0, "total": 0,
                "version": _version}


def rebuild(source=None, resume=True, should_stop=None, sample_size=16):
    """全量重建：旁路构建新索引，校验通过后原子切换。

    source: 可迭代的 (key, value) 对或返回它的 callable；缺省用当前索引。
    should_stop: 每条目前回调，返回 True 则中断并写断点，下次可续跑。
    返回 dict：rebuilt/switched/checked(旧字段恒 False)/validated/validation。
    """
    global _current, _pending, _rebuild, _version
    _ensure_loaded()
    with _lock:
        base_version = _version
        if source is None:
            src = {k: ent["value"] for k, ent in _current.items()}
        else:
            raw = source() if callable(source) else source
            src = {}
            for k, v in raw:
                src[k] = v

    fingerprint = hashlib.sha256(
        json.dumps(src, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    items = sorted(src.items())

    # 断点续跑：数据源指纹一致才复用已建部分
    built = {}
    processed = 0
    ckpt = _read_json(_paths()["checkpoint"]) if resume else None
    if ckpt and ckpt.get("fingerprint") == fingerprint:
        built = dict(ckpt.get("entries", {}))
        processed = int(ckpt.get("processed", 0))
    resumed_from = processed

    with _lock:
        _rebuild = {"state": "rebuilding", "processed": processed,
                    "total": len(items), "started_at": time.time()}

    def interrupted(reason):
        global _rebuild
        _write_ckpt(fingerprint, built, processed, len(items))
        with _lock:
            _rebuild = {"state": "interrupted", "processed": processed,
                        "total": len(items), "resumable": True}
        return {"rebuilt": processed, "switched": False, "checked": False,
                "validated": False, "interrupted": True, "resumable": True,
                "resumed_from": resumed_from, "reason": reason}

    try:
        for i, (k, v) in enumerate(items):
            if i < processed:
                continue
            if should_stop is not None and should_stop():
                raise _Interrupt("should_stop")
            built[k] = {"value": v, "version": base_version}
            processed += 1
            with _lock:
                if _rebuild is not None:
                    _rebuild["processed"] = processed
            if processed % _checkpoint_every == 0:
                _write_ckpt(fingerprint, built, processed, len(items))
    except _Interrupt as exc:
        return interrupted(str(exc))
    except KeyboardInterrupt:
        return interrupted("keyboard")

    # 校验：条目数 + 抽样内容比对
    expected = len(src)
    step = max(1, expected // max(1, sample_size))
    sample_keys = [k for k, _ in items[::step]][:sample_size] if expected else []
    mismatches = [k for k in sample_keys
                  if k not in built or built[k]["value"] != src[k]]
    validation = {
        "entry_count": len(built),
        "expected_count": expected,
        "sampled": len(sample_keys),
        "sample_mismatches": mismatches,
        "ok": len(built) == expected and not mismatches,
    }

    if not validation["ok"]:
        _rollback("校验失败: 条目数 %d/%d, 抽样不符 %d 个"
                  % (len(built), expected, len(mismatches)))
        with _lock:
            _rebuild = None
        _discard_ckpt()
        return {"rebuilt": len(built), "switched": False, "checked": False,
                "validated": False, "validation": validation,
                "rolled_back": True, "resumed_from": resumed_from}

    # 原子切换：先合并增量、落盘成功后才替换服务中的索引
    with _lock:
        merged = dict(built)
        for k, ent in _pending.items():
            cur = merged.get(k)
            if cur is None or ent["version"] >= cur["version"]:
                merged[k] = ent
        _version += 1
        try:
            _write_atomic(_paths()["index"], {"entries": merged})
            _write_atomic(_paths()["meta"], {
                "version": _version,
                "entry_count": len(merged),
                "updated_at": time.time(),
            })
        except Exception as exc:  # 切换失败，保留旧索引
            _rollback("切换失败: %s" % exc)
            _rebuild = None
            return {"rebuilt": len(built), "switched": False,
                    "checked": False, "validated": True,
                    "validation": validation, "rolled_back": True,
                    "resumed_from": resumed_from}
        _current = merged
        _pending = {}
        _rebuild = None
    _discard_ckpt()
    return {"rebuilt": len(merged), "switched": True, "checked": False,
            "validated": True, "validation": validation,
            "version": _version, "resumed_from": resumed_from}
