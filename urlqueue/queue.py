"""链接优先级队列：老化提权 + 保底窗口 + 落盘恢复。

分数口径: score = 基础优先级 + AGING_PER_SECOND * 等待秒数, 分高先出, 同分先入先出。
保底口径: 每 GUARANTEE_WINDOW 次出队至少 1 次给等待最久的条目,
          等价于高优连续插队上限 = GUARANTEE_WINDOW - 1 次。
"""

import json
import os
import tempfile
import time

AGING_PER_SECOND = 1.0 / 60.0  # 每等待 60 秒 +1 分
GUARANTEE_WINDOW = 5           # 保底窗口: 每 5 次出队至少 1 次保底
DEFAULT_STATE_PATH = "urlqueue_state.json"

_queue = []                # [{"url", "priority", "enqueued_at", "seq"}]
_seq = 0                   # 入队序号, 同分时按它先入先出
_pops_since_guarantee = 0  # 距上次保底出队, 已连续按分数出队的次数
_guaranteed = 0            # 累计保底出队次数
_restored = 0              # 上次从磁盘恢复的条数
_clock = time.time         # 时钟, 测试可替换
_state_path = os.environ.get("URLQUEUE_STATE", DEFAULT_STATE_PATH)


def configure(state_path=None, aging_per_second=None, guarantee_window=None):
    """调整参数; 指定 state_path 时立即从该文件恢复。"""
    global AGING_PER_SECOND, GUARANTEE_WINDOW, _state_path
    if aging_per_second is not None:
        AGING_PER_SECOND = aging_per_second
    if guarantee_window is not None:
        GUARANTEE_WINDOW = guarantee_window
    if state_path is not None:
        _state_path = state_path
        load()


def _score(entry, now):
    wait = max(0.0, now - entry["enqueued_at"])
    return entry["priority"] + AGING_PER_SECOND * wait


def push(url, priority=5):
    """入队, 记录入队时间和基础优先级, 返回当前队列长度。"""
    global _seq
    _queue.append({
        "url": url,
        "priority": priority,
        "enqueued_at": _clock(),
        "seq": _seq,
    })
    _seq += 1
    _save()
    return len(_queue)


def pop(now=None):
    """出队分数最高者; 到达保底窗口时改为等待最久者。空队列返回 None。"""
    global _pops_since_guarantee, _guaranteed
    if not _queue:
        return None
    now = _clock() if now is None else now
    if _pops_since_guarantee >= GUARANTEE_WINDOW - 1:
        # 保底: 等待最久(序号最小)者出队, 高优连续插队到此为止
        idx = min(range(len(_queue)), key=lambda i: _queue[i]["seq"])
        _pops_since_guarantee = 0
        _guaranteed += 1
    else:
        # 同分按入队顺序: 分数相同取序号最小者
        idx = max(range(len(_queue)),
                  key=lambda i: (_score(_queue[i], now), -_queue[i]["seq"]))
        _pops_since_guarantee += 1
    entry = _queue.pop(idx)
    _save()
    return {
        "url": entry["url"],
        "priority": entry["priority"],
        "score": round(_score(entry, now), 6),
    }


def stats(now=None):
    """当前分数、提权条数、保底次数、恢复条数。"""
    now = _clock() if now is None else now
    scores = sorted(
        ((e["url"], round(_score(e, now), 6)) for e in _queue),
        key=lambda kv: -kv[1],
    )
    promoted = sum(1 for e in _queue if _score(e, now) - e["priority"] >= 1.0)
    return {
        "size": len(_queue),
        "scores": scores,
        "promoted": promoted,
        "guaranteed": _guaranteed,
        "restored": _restored,
    }


def _save():
    """原子落盘: 先写临时文件再替换。"""
    if not _state_path:
        return
    data = {
        "seq": _seq,
        "pops_since_guarantee": _pops_since_guarantee,
        "guaranteed": _guaranteed,
        "entries": _queue,
    }
    directory = os.path.dirname(os.path.abspath(_state_path))
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _state_path)


def load():
    """从磁盘恢复, 按当前分数重排, 返回恢复条数。"""
    global _seq, _pops_since_guarantee, _guaranteed, _restored
    _restored = 0
    if not _state_path or not os.path.exists(_state_path):
        return 0
    with open(_state_path, encoding="utf-8") as f:
        data = json.load(f)
    _queue[:] = data.get("entries", [])
    _seq = data.get("seq", len(_queue))
    _pops_since_guarantee = data.get("pops_since_guarantee", 0)
    _guaranteed = data.get("guaranteed", 0)
    now = _clock()
    _queue.sort(key=lambda e: (-_score(e, now), e["seq"]))
    _restored = len(_queue)
    return _restored


load()
