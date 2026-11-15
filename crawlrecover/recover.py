"""采集崩溃恢复：位点与校验和落盘，崩溃后从位点续跑。

口径详见 README.md：
- 每个任务一份追加式日志，每行一条带序号/批次号/时间戳/校验和的 JSON 记录；
- recover 从上次位点继续校验，半行截断、坏记录跳过、已完成页不再重复写；
- 恢复可中断，进度随位点落盘，再次恢复从同一位置继续；重复恢复幂等。
"""

import hashlib
import json
import os
from datetime import datetime, timezone

__all__ = ["mark", "recover"]

# 测试/演示钩子：恢复中每处理完一条日志记录调用一次（keyword 参数），
# 抛出 KeyboardInterrupt 可模拟恢复中断。正常使用时保持 None。
PROGRESS_HOOK = None


def _home():
    return os.environ.get("CRAWLRECOVER_HOME") or os.path.join(
        os.getcwd(), ".crawlrecover")


def _paths(task):
    base = os.path.join(_home(), task)
    return base + ".journal", base + ".recover.json", base + ".recoverlog"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _new_batch():
    return "b-{}-{}".format(
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"), os.getpid())


def _checksum(seq, batch, ts, page):
    raw = "{}|{}|{}|{}".format(seq, batch, ts, page).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _make_record(seq, batch, page):
    ts = _now()
    return {"seq": seq, "batch": batch, "ts": ts, "page": page,
            "checksum": _checksum(seq, batch, ts, page)}


def _valid(rec):
    return isinstance(rec, dict) and rec.get("checksum") == _checksum(
        rec.get("seq"), rec.get("batch"), rec.get("ts"), rec.get("page"))


def _scan(journal, from_offset=0):
    """扫描 [from_offset, EOF)，返回 (entries, truncated)。

    entries: [(offset, end, record|None, error|None)]，error 为
    "bad_json"/"bad_checksum"；truncated 为 None 或
    {"offset", "dropped"}（文件末尾无换行的半行）。
    """
    with open(journal, "rb") as f:
        f.seek(from_offset)
        data = f.read()
    entries, truncated = [], None
    chunks = data.split(b"\n")
    if data and not data.endswith(b"\n"):
        tail = chunks.pop()
        truncated = {"offset": from_offset + len(data) - len(tail),
                     "dropped": len(tail)}
    pos = from_offset
    for chunk in chunks:
        end = pos + len(chunk) + 1
        if chunk.strip():
            try:
                rec = json.loads(chunk.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                entries.append((pos, end, None, "bad_json"))
            else:
                if _valid(rec):
                    entries.append((pos, end, rec, None))
                else:
                    entries.append((pos, end, None, "bad_checksum"))
        pos = end
    return entries, truncated


def _load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _log_event(path, event):
    event = dict(event)
    event.setdefault("ts", _now())
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _journal_batch(journal):
    if not os.path.exists(journal):
        return None
    entries, _ = _scan(journal)
    for _off, _end, rec, err in entries:
        if rec is not None:
            return rec["batch"]
    return None


def _reset_batch(journal, state_path, log_path, old_batch, new_batch):
    """位点不属于当前批次：归档旧日志，清掉旧位点，从头开始。"""
    if os.path.exists(journal):
        os.replace(journal, "{}.{}.bak".format(journal, old_batch or "unknown"))
    if os.path.exists(state_path):
        os.remove(state_path)
    _log_event(log_path, {"event": "reset",
                          "old_batch": old_batch, "new_batch": new_batch})


def mark(task, page, batch=None):
    """把已完成页面追加进日志（带序号/批次号/时间戳/校验和）。

    同一批次内重复 mark 幂等，不落重复记录；指定 batch 与日志当前批次
    不符时归档旧日志、重开新批次。
    """
    os.makedirs(_home(), exist_ok=True)
    journal, state_path, log_path = _paths(task)
    last_seq, cur_batch, done = 0, None, set()
    if os.path.exists(journal):
        entries, truncated = _scan(journal)
        if truncated:  # 上次崩溃留下半行，先截断再追加
            with open(journal, "r+b") as f:
                f.truncate(truncated["offset"])
        for _off, _end, rec, err in entries:
            if rec is not None:
                last_seq = rec["seq"]
                cur_batch = rec["batch"]
                done.add(rec["page"])
    if batch is not None and cur_batch is not None and batch != cur_batch:
        _reset_batch(journal, state_path, log_path, cur_batch, batch)
        last_seq, cur_batch, done = 0, None, set()
    if page in done:
        return {"task": task, "marked": page, "duplicate": True,
                "seq": None, "batch": cur_batch}
    cur_batch = batch or cur_batch or _new_batch()
    rec = _make_record(last_seq + 1, cur_batch, page)
    with open(journal, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return {"task": task, "marked": page, "duplicate": False,
            "seq": rec["seq"], "batch": cur_batch}


def recover(task, batch=None):
    """从位点恢复：校验日志、截断半行、跳过坏记录，返回恢复报告。

    报告含恢复位点 position、已完成键 completed（调用方据此跳过）、
    坏记录 skipped、截断记录 truncated。重复调用幂等；中断后再次调用
    从同一位置继续。指定 batch 与日志批次不符时判定位点不属于当前
    批次，归档后从头开始。
    """
    os.makedirs(_home(), exist_ok=True)
    journal, state_path, log_path = _paths(task)
    state = _load_state(state_path)
    journal_size = os.path.getsize(journal) if os.path.exists(journal) else 0

    batch_reset = False
    if batch is not None:
        cur = state["batch"] if state else _journal_batch(journal)
        if cur is not None and cur != batch:
            _reset_batch(journal, state_path, log_path, cur, batch)
            state, journal_size, batch_reset = None, 0, True

    if (state and state.get("status") == "done"
            and state.get("next_offset") == journal_size):
        return dict(state["report"])  # 日志未变，幂等返回上次报告

    attempt = state["attempt"] + 1 if state else 1
    from_offset = state["next_offset"] if state else 0
    last_seq = state["last_seq"] if state else 0
    done = list(state["done"]) if state else []
    done_set = set(done)
    cur_batch = (state["batch"] if state else None) or batch
    position = state["position"] if state else None
    skipped, truncated, interrupted = [], None, False

    _log_event(log_path, {"event": "begin", "attempt": attempt,
                          "from": from_offset})

    entries, truncated = ([], None)
    if journal_size > from_offset:
        entries, truncated = _scan(journal, from_offset)
    end = from_offset
    try:
        for off, rec_end, rec, err in entries:
            end = rec_end
            if err is not None:
                skipped.append({"offset": off, "reason": err})
            elif rec["seq"] <= last_seq:
                skipped.append({"offset": off, "reason": "stale_seq",
                                "seq": rec["seq"]})
            else:
                if rec["seq"] != last_seq + 1:
                    skipped.append({"offset": off, "reason": "seq_gap",
                                    "expected": last_seq + 1,
                                    "got": rec["seq"]})
                last_seq = rec["seq"]
                cur_batch = rec["batch"]
                if rec["page"] not in done_set:
                    done_set.add(rec["page"])
                    done.append(rec["page"])
                position = {"seq": rec["seq"], "offset": rec_end,
                            "batch": rec["batch"], "ts": rec["ts"]}
            _save_state(state_path, {
                "attempt": attempt, "batch": cur_batch, "last_seq": last_seq,
                "next_offset": end, "done": done, "position": position,
                "status": "in_progress"})
            if PROGRESS_HOOK is not None:
                PROGRESS_HOOK(task=task, offset=end)
    except KeyboardInterrupt:
        interrupted = True
        _log_event(log_path, {"event": "interrupted", "attempt": attempt,
                              "offset": end})

    if truncated and not interrupted:
        with open(journal, "r+b") as f:
            f.truncate(truncated["offset"])
        end = truncated["offset"]

    report = {"task": task, "batch": cur_batch, "batch_reset": batch_reset,
              "attempt": attempt, "start": from_offset, "end": end,
              "position": position, "completed": done, "skipped": skipped,
              "truncated": truncated if truncated else False,
              "interrupted": interrupted}
    if interrupted:
        return report  # 位点已逐条落盘，下次从同一位置继续

    _log_event(log_path, {"event": "done", "attempt": attempt,
                          "from": from_offset, "to": end})
    _save_state(state_path, {
        "attempt": attempt, "batch": cur_batch, "last_seq": last_seq,
        "next_offset": end, "done": done, "position": position,
        "status": "done", "report": report})
    return report
