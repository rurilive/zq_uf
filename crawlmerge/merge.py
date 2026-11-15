"""按主键与版本合并采集分片，支持空值保护、冲突双侧保留与断点续合。

合并口径：
- 主键取 row["id"]，版本取 row.get("version", 0)，同主键版本高者优先；
- None 视为空值，空值永不覆盖有效值；低版本行的有效值只补高版本行的空字段；
- 同主键同版本、且存在非空字段取值不一致时记为冲突：主表后到先写，冲突区保留两侧；
- 分片可用 {"shard": 分片号, "rows": [...]} 标号，裸列表按进程内序号自动编号；
- 合并清单按分片号排序，逐分片落断点；重复分片按已合并位置跳过，整体幂等。
"""

import json
import os
import re
import tempfile
from datetime import datetime

PK = "id"
VERSION_KEY = "version"
DEFAULT_BATCH = "default"

_rows = {}          # 主键 -> 合并后的行
_conflicts = {}     # 主键 -> {"id", "sides": [{"version", "row"}]}
_manifest = {}      # 分片号 -> {"shard", "position", "rows", "fingerprint"}
_batch = None
_state_dir = None
_loaded_token = None
_bare_seq = 0
_fail_after = None


# ---------- 基础工具 ----------

def _is_empty(value):
    """空值口径：仅 None 为空，0/False/空串等都是有效值。"""
    return value is None


def _version(row):
    value = row.get(VERSION_KEY, 0)
    return 0 if _is_empty(value) else value


def _fingerprint(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def _default_state_dir():
    return os.path.join(tempfile.gettempdir(), "crawlmerge")


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name)) or "default"


def _checkpoint_path(state_dir, batch):
    return os.path.join(state_dir, "checkpoint_%s.json" % _safe_name(batch))


def _reset():
    """清空全部进程内状态，供测试与新批次隔离使用。"""
    global _rows, _conflicts, _manifest, _batch, _state_dir
    global _loaded_token, _bare_seq, _fail_after
    _rows = {}
    _conflicts = {}
    _manifest = {}
    _batch = None
    _state_dir = None
    _loaded_token = None
    _bare_seq = 0
    _fail_after = None


# ---------- 分片规整 ----------

def _normalize_shards(shards):
    """把多种分片写法统一成 (分片号, 行列表)；裸分片分配进程内唯一序号。"""
    global _bare_seq
    normalized = []
    for shard in shards:
        if isinstance(shard, dict) and ("rows" in shard or "data" in shard):
            number = shard.get("shard", shard.get("shard_id", shard.get("no")))
            if number is None:
                number, _bare_seq = _bare_seq, _bare_seq + 1
            rows = shard.get("rows", shard.get("data"))
        elif (
            isinstance(shard, (tuple, list))
            and len(shard) == 2
            and isinstance(shard[1], (list, tuple))
            and not isinstance(shard[0], (dict, list, tuple))
        ):
            number, rows = shard
        else:
            number, _bare_seq = _bare_seq, _bare_seq + 1
            rows = shard
        normalized.append((number, list(rows)))
    return normalized


# ---------- 行级合并 ----------

def _field_merge(high, low):
    """高版本行为底，低版本行的有效值只补空字段（双向空值保护）。"""
    merged = dict(high)
    for key, value in low.items():
        if key in (PK, VERSION_KEY) or _is_empty(value):
            continue
        if key not in merged or _is_empty(merged[key]):
            merged[key] = value
    return merged


def _field_merge_last_wins(old, new):
    """同版本合并：旧字段保留，新行非空值后到先写，空值不覆盖。"""
    merged = dict(old)
    for key, value in new.items():
        if key in (PK, VERSION_KEY) or _is_empty(value):
            continue
        merged[key] = value
    return merged


def _has_value_conflict(left, right):
    """同版本下，两侧同一非空字段取值不同即冲突。"""
    for key in set(left) | set(right):
        if key in (PK, VERSION_KEY):
            continue
        lv, rv = left.get(key), right.get(key)
        if not _is_empty(lv) and not _is_empty(rv) and lv != rv:
            return True
    return False


def _record_conflict(key, left, right):
    record = _conflicts.setdefault(key, {"id": key, "sides": []})
    for side in (left, right):
        fingerprint = _fingerprint(side)
        if not any(_fingerprint(item["row"]) == fingerprint for item in record["sides"]):
            record["sides"].append({"version": _version(side), "row": dict(side)})


def _apply_row(row):
    key = row[PK]
    old = _rows.get(key)
    if old is None:
        _rows[key] = dict(row)
        return
    new = dict(row)
    old_v, new_v = _version(old), _version(new)
    if new_v > old_v:
        _rows[key] = _field_merge(high=new, low=old)
    elif new_v < old_v:
        _rows[key] = _field_merge(high=old, low=new)
    elif _fingerprint(old) == _fingerprint(new):
        return  # 完全重复，幂等无操作
    else:
        # 同版本：空值保护后的后到先写；有非空分歧才记冲突并保留两侧
        if _has_value_conflict(old, new):
            _record_conflict(key, old, new)
        _rows[key] = _field_merge_last_wins(old, new)


# ---------- 断点持久化 ----------

def _write_checkpoint():
    os.makedirs(_state_dir, exist_ok=True)
    path = _checkpoint_path(_state_dir, _batch)
    payload = {
        "batch": _batch,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": list(_rows.values()),
        "conflicts": _conflicts_output(),
        "manifest": _manifest_output(),
    }
    fd, tmp = tempfile.mkstemp(prefix=".checkpoint-", dir=_state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def _load_checkpoint_file(path, mark_resume=False):
    global _loaded_token
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    _rows.clear()
    for row in payload.get("rows", []):
        _rows[row[PK]] = row
    _conflicts.clear()
    for record in payload.get("conflicts", []):
        _conflicts[record[PK]] = record
    _manifest.clear()
    for entry in payload.get("manifest", []):
        _manifest[entry["shard"]] = entry
    if mark_resume:
        _loaded_token = path


def _load_state(batch, state_dir):
    global _batch, _state_dir
    _batch = batch or DEFAULT_BATCH
    _state_dir = state_dir or _default_state_dir()
    path = _checkpoint_path(_state_dir, _batch)
    if os.path.exists(path):
        _load_checkpoint_file(path)


def _resolve_token(token):
    if isinstance(token, dict):
        token = token.get("file") or token.get("token") or _checkpoint_path(
            token.get("state_dir") or _default_state_dir(),
            token.get("batch", DEFAULT_BATCH),
        )
    if os.path.exists(token):
        return token
    path = _checkpoint_path(_state_dir or _default_state_dir(), token)
    if os.path.exists(path):
        return path
    raise FileNotFoundError("找不到断点文件: %s" % token)


# ---------- 输出结构 ----------

def _manifest_output():
    return [_manifest[number] for number in sorted(_manifest)]


def _conflicts_output():
    return [_conflicts[key] for key in sorted(_conflicts, key=lambda item: str(item))]


def _done_shards():
    return sum(1 for entry in _manifest.values() if entry["position"] >= entry["rows"])


def _checkpoint_info(path):
    return {
        "batch": _batch,
        "file": path,
        "token": path,
        "manifest": _manifest_output(),
    }


def _summary(path, processed):
    return {
        "rows": len(_rows),
        "merged": len(_rows),
        "processed": processed,
        "conflicts": _conflicts_output(),
        "checkpoint": _checkpoint_info(path),
        "token": path,
    }


# ---------- 对外接口 ----------

def merge(shards, batch=None, state_dir=None):
    """按分片号排序后逐行合并，逐分片落断点；失败时保留已合并位置后抛出。"""
    _load_state(batch, state_dir)
    ordered = sorted(_normalize_shards(shards), key=lambda item: item[0])
    processed = 0
    path = _checkpoint_path(_state_dir, _batch)
    try:
        for number, rows in ordered:
            entry = _manifest.setdefault(
                number,
                {"shard": number, "position": 0, "rows": len(rows), "fingerprint": ""},
            )
            entry["rows"] = len(rows)
            entry["fingerprint"] = _fingerprint(rows)
            for index in range(entry["position"], len(rows)):
                if _fail_after is not None and processed >= _fail_after:
                    raise RuntimeError("模拟失败：已处理 %d 行" % processed)
                _apply_row(rows[index])
                entry["position"] = index + 1
                processed += 1
            path = _write_checkpoint()  # 每个分片边界落一次断点
    except BaseException:
        path = _write_checkpoint()      # 失败也落断点，保留分片内已合并位置
        raise
    path = _write_checkpoint()
    return _summary(path, processed)


def resume(token=None):
    """从断点恢复：传入 merge 返回的 token（断点文件路径或批次名）。

    无 token 时没有明确的恢复目标，返回零值进度；跨进程续合请持久化并回传 token。
    """
    if token is None:
        if _loaded_token is None:
            return {
                "done": 0,
                "rows": 0,
                "conflicts": [],
                "checkpoint": None,
                "token": None,
                "batch": None,
            }
        path = _loaded_token
    else:
        path = _resolve_token(token)
        _load_checkpoint_file(path, mark_resume=True)
    return {
        "done": _done_shards(),
        "rows": len(_rows),
        "conflicts": _conflicts_output(),
        "checkpoint": _checkpoint_info(path),
        "token": path,
        "batch": _batch,
    }
