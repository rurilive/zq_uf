"""审计日志哈希链。

每条记录的摘要覆盖「本条内容 + 上一条摘要（link）」，因此：

* 记录内容被改动、链接值被改动都会在逐条校验时暴露；
* 按天切分时，每天首条记录的 link 就是前一天尾条的摘要，跨天衔接值不会丢；
* 校验只需要目标区间内的记录，瓶颈与区间长度成正比，不必全量重算；
* 一次校验可以限制条数，返回的 resume 令牌能接着上次的位置继续算；
* 摘要算法记在每条记录上，算法升级后旧区间仍然按旧算法验证。
"""

import copy
import hashlib
import json

_records = []

_DEFAULT_ALGORITHM = "sha256"
# 名称 -> 接收 bytes、返回十六进制摘要字符串的可调用对象
_ALGORITHMS = {}


# ---------------------------------------------------------------------------
# 摘要算法
# ---------------------------------------------------------------------------

def _resolve_algorithm(name):
    name = name or _DEFAULT_ALGORITHM
    func = _ALGORITHMS.get(name)
    if func is not None:
        return name, func
    try:
        hashlib.new(name)
    except (TypeError, ValueError):
        raise ValueError("unknown digest algorithm: %r" % (name,))

    def _builtin(payload, _name=name):
        return hashlib.new(_name, payload).hexdigest()

    _ALGORITHMS[name] = _builtin
    return name, _builtin


def register_algorithm(name, func):
    """注册自定义摘要算法，用于算法迁移后继续验证旧链接。

    func 接收 bytes，返回十六进制摘要（str 或 bytes）；也可传 hashlib 支持的算法名。
    """
    if not isinstance(name, str) or not name:
        raise ValueError("algorithm name must be a non-empty string")
    if isinstance(func, str):
        base = func

        def func(payload, _base=base):
            return hashlib.new(_base, payload).hexdigest()

    if not callable(func):
        raise ValueError("algorithm must be a callable or a hashlib name")
    _ALGORITHMS[name] = func
    return name


def set_algorithm(name):
    """切换后续 append 使用的默认摘要算法，已有记录仍按各自记录的算法校验。"""
    global _DEFAULT_ALGORITHM
    _resolve_algorithm(name)
    _DEFAULT_ALGORITHM = name
    return name


def get_algorithm():
    return _DEFAULT_ALGORITHM


def _hash(algorithm, payload):
    name, func = _resolve_algorithm(algorithm)
    result = func(payload)
    if isinstance(result, (bytes, bytearray)):
        result = bytes(result).hex()
    if not isinstance(result, str) or not result:
        raise ValueError("digest algorithm %r returned an invalid value" % (name,))
    return result


def _canonical(record):
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(record, link="", algorithm=None, day=None, time=None, seq=None):
    """计算摘要：算法名、seq、前一条摘要、按天切分用的 day/time、规范化后的记录内容。

    seq 和 day/time 也纳入覆盖，避免改序号或改写分段归属后不被发现。
    """
    algorithm = algorithm or _DEFAULT_ALGORITHM
    if day is None and isinstance(record, dict):
        day = record.get("day", "")
    if time is None:
        time = _time_of(record)
    if seq is None:
        seq = ""
    material = "%s\n%s\n%s\n%s\n%s\n%s" % (
        algorithm, seq, link or "", day, time, _canonical(record)
    )
    return _hash(algorithm, material.encode("utf-8"))


def _time_of(record):
    if not isinstance(record, dict):
        return ""
    for key in ("time", "ts", "timestamp", "day"):
        value = record.get(key)
        if value not in (None, ""):
            return value
    return ""


def _entry_algorithm(entry, fallback=None):
    if isinstance(entry, dict) and entry.get("algorithm"):
        return entry["algorithm"]
    return fallback or _DEFAULT_ALGORITHM


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def append(record, algorithm=None):
    """追加一条记录。

    返回 seq、day、time、digest（本条摘要）、link（上一条摘要，首条为空串）和算法。
    """
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    algorithm = algorithm or _DEFAULT_ALGORITHM
    _resolve_algorithm(algorithm)

    link = _field(_records[-1], "digest") if _records else ""
    seq = len(_records)
    day = record.get("day", "")
    time = _time_of(record)
    digest = _digest(record, link, algorithm, day, time, seq)
    entry = {
        "seq": seq,
        "day": day,
        "time": time,
        "record": copy.deepcopy(record),
        "digest": digest,
        "link": link,
        "algorithm": algorithm,
    }
    _records.append(entry)
    return {
        "seq": entry["seq"],
        "day": entry["day"],
        "time": entry["time"],
        "digest": digest,
        "link": link,
        "algorithm": algorithm,
    }


# ---------------------------------------------------------------------------
# 区间解析
# ---------------------------------------------------------------------------

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _seq_bounds(start, end):
    if start is not None and start < 0:
        raise ValueError("start must not be negative")
    if end is not None and end < 0:
        raise ValueError("end must not be negative")
    if start is not None and end is not None and start > end:
        raise ValueError("start must not be after end")
    if not _records:
        if start is not None:
            raise ValueError("span starts past the end of an empty chain")
        return None, None
    last = len(_records) - 1
    if start is None:
        start = 0
    if end is None:
        end = last
    if start > last:
        raise ValueError(
            "span starts past the end of the chain (last seq is %d)" % last
        )
    end = min(end, last)
    return start, end


def _day_bounds(start_day, end_day):
    for value in (start_day, end_day):
        if value is not None and not isinstance(value, str):
            raise ValueError("day bounds must be strings")
    if start_day is not None and end_day is not None and start_day > end_day:
        raise ValueError("start_day must not be after end_day")
    hits = []
    for index, entry in enumerate(_records):
        day = entry.get("day", "")
        if not day or not isinstance(day, str):
            continue
        if start_day is not None and day < start_day:
            continue
        if end_day is not None and day > end_day:
            continue
        hits.append(index)
    if not hits:
        return None, None
    return hits[0], hits[-1]


def _first(value, *keys):
    for key in keys:
        if key in value:
            return value[key]
    return None


def _day_list_bounds(days):
    if isinstance(days, str) or not days:
        raise ValueError("days must be a non-empty list of day strings")
    for value in days:
        if not isinstance(value, str):
            raise ValueError("days must contain day strings")
    wanted = set(days)
    hits = [index for index, entry in enumerate(_records) if entry.get("day", "") in wanted]
    if not hits:
        return None, None
    return hits[0], hits[-1]


def _dict_bounds(span):
    day_keys = ("day", "days", "start_day", "from_day", "end_day", "to_day")
    seq_keys = ("start", "from", "start_seq", "end", "to", "end_seq")
    has_day = any(key in span for key in day_keys)
    has_seq = any(key in span for key in seq_keys)
    unknown = [key for key in span
               if key not in day_keys and key not in seq_keys]
    if unknown:
        raise ValueError("unknown span keys: %s" % ", ".join(sorted(map(str, unknown))))
    if has_day and has_seq:
        raise ValueError("cannot mix seq range and day range in one span")

    if has_day:
        if "days" in span:
            if len(span) != 1:
                raise ValueError("days cannot be combined with other day keys")
            return _day_list_bounds(span["days"])
        start_day = _first(span, "start_day", "from_day")
        end_day = _first(span, "end_day", "to_day")
        single = span.get("day")
        if single is not None:
            if start_day is not None or end_day is not None:
                raise ValueError("day cannot be combined with start_day/end_day")
            start_day = end_day = single
        return _day_bounds(start_day, end_day)

    if has_seq:
        start = _first(span, "start", "from", "start_seq")
        end = _first(span, "end", "to", "end_seq")
        if start is not None and not _is_int(start):
            raise ValueError("start must be an integer seq")
        if end is not None and not _is_int(end):
            raise ValueError("end must be an integer seq")
        return _seq_bounds(start, end)

    return _seq_bounds(None, None)


def _span_bounds(span):
    if span is None:
        return _seq_bounds(None, None)
    if _is_int(span):
        return _seq_bounds(span, span)
    if isinstance(span, str):
        return _day_bounds(span, span)
    if isinstance(span, (tuple, list)):
        if len(span) == 1:
            return _span_bounds(span[0])
        if len(span) != 2:
            raise ValueError("span must have one or two items")
        start, end = span
        if _is_int(start) and _is_int(end):
            return _seq_bounds(start, end)
        if isinstance(start, str) and isinstance(end, str):
            return _day_bounds(start, end)
        raise ValueError("span items must be two seq numbers or two day strings")
    if isinstance(span, dict):
        return _dict_bounds(span)
    raise ValueError("span must be None, int, str, a pair, or a dict")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _field(entry, key, default=""):
    if isinstance(entry, dict):
        value = entry.get(key, default)
        if value is not None:
            return value
    return default


def _brief(entry):
    if not isinstance(entry, dict):
        return None
    record = entry.get("record")
    return {
        "seq": entry.get("seq"),
        "day": entry.get("day", ""),
        "time": entry.get("time", ""),
        "digest": entry.get("digest", ""),
        "link": entry.get("link", ""),
        "algorithm": _entry_algorithm(entry),
        "record": copy.deepcopy(record),
    }


def _empty_result():
    return {
        "ok": True,
        "found": False,
        "checked": 0,
        "checked_total": 0,
        "start": None,
        "end": None,
        "requested_start": None,
        "requested_end": None,
        "digest": None,
        "link": "",
        "days": [],
        "broken": None,
        "broken_time": None,
        "reason": None,
        "break": None,
        "impact": None,
        "anchor": None,
        "resume": None,
        "complete": True,
    }


def _days_in(start, end):
    """区间覆盖的日期集合（按出现顺序），供审计同学确认区间是否跨天。"""
    out = []
    for entry in _records[start:end + 1]:
        day = _field(entry, "day")
        if day not in out:
            out.append(day)
    return out


def _check_entry(entry, expected_link, fallback_algorithm=None):
    """校验单条记录，返回 (reason, digest)；通过时 reason 为 None。

    reason 为 "digest" 表示内容与摘要对不上，"link" 表示摘要自洽但接不上前一条。
    """
    if not isinstance(entry, dict) or not isinstance(entry.get("record"), dict):
        return "digest", entry.get("digest", "") if isinstance(entry, dict) else ""
    algorithm = _entry_algorithm(entry, fallback_algorithm)
    stored_digest = entry.get("digest", "")
    stored_link = entry.get("link", "")
    recomputed = _digest(
        entry.get("record", {}),
        stored_link,
        algorithm,
        entry.get("day", ""),
        entry.get("time", ""),
        entry.get("seq"),
    )
    if recomputed != stored_digest:
        return "digest", stored_digest
    if stored_link != expected_link:
        return "link", stored_digest
    return None, stored_digest


def verify(span=None, resume=None, limit=None, algorithm=None, link=None):
    """校验指定区间，可从断点续算。

    span 支持：
      * None                       —— 全链
      * 3、(0, 9)                  —— 序号闭区间
      * "2026-01-02"               —— 单天
      * ("2026-01-01", "2026-01-03") —— 跨天闭区间
      * {"start": 0, "end": 9}     —— 序号闭区间
      * {"start_day": "...", "end_day": "..."} / {"day": "..."} / {"days": [...]}

    link 给出区间首条应当接上的摘要（例如前一天的尾摘要），只校验单天时也能证明跨天衔接。
    limit 限制本次校验条数；返回的 resume 令牌原样传回即可接着算。
    algorithm 只作为旧记录缺少算法字段时的回退值。

    日期区间假设记录按天有序（追加写入即如此）；乱序补录的记录请用序号区间。
    """
    if limit is not None and (not _is_int(limit) or limit <= 0):
        raise ValueError("limit must be a positive integer")

    if resume is not None and link is not None:
        raise ValueError("link cannot be combined with resume")

    if resume is not None:
        if not isinstance(resume, dict):
            raise ValueError("resume must be a dict returned by verify")
        if resume.get("v") != 1:
            raise ValueError("unsupported resume token")
        start = resume.get("start")
        end = resume.get("end")
        cursor = resume.get("next")
        expected_link = resume.get("link", "")
        range_link = resume.get("range_link", "")
        checked_total = resume.get("checked", 0)
        algorithm = resume.get("algorithm", algorithm)
        if start is None or not _is_int(cursor) or not _is_int(end):
            raise ValueError("invalid resume token")
        # 先确认链没有变短，否则区间解析会先抛出一个看不出原因的错误
        if end >= len(_records):
            raise ValueError(
                "chain is shorter than the resume token's range; "
                "records may have been removed"
            )
        if cursor > len(_records):
            raise ValueError("resume token is past the end of the chain")
        if span is not None:
            # 允许区间尾部因为新追加记录而变长，但不允许换起点或把区间缩短
            span_start, span_end = _span_bounds(span)
            if span_start != start or span_end is None or span_end < end:
                raise ValueError("span does not match resume token")
        # 令牌绑在已校验到的记录上：续算前复核断点前一条仍然自洽且接得上，
        # 前缀若在两次校验之间被改动就拒绝续算，而不是拿旧令牌接着算出假的通过。
        if cursor > 0:
            prev_entry = _records[cursor - 1]
            prev_link = _field(_records[cursor - 2], "digest") if cursor >= 2 else ""
            prev_reason, prev_digest = _check_entry(prev_entry, prev_link, algorithm)
            if prev_reason is not None or prev_digest != expected_link:
                raise ValueError(
                    "resume token does not match the chain; "
                    "records before the resume point changed"
                )
    else:
        start, end = _span_bounds(span)
        checked_total = 0
        if start is None:
            return _empty_result()
        cursor = start
        if link is not None:
            expected_link = link
        elif start == 0:
            expected_link = ""
        else:
            expected_link = _field(_records[start - 1], "digest")
        range_link = expected_link

    checked_this = 0
    last_seq = None
    last_digest = None
    reason = None
    broken = None

    while cursor <= end:
        if limit is not None and checked_this >= limit:
            break

        entry = _records[cursor]
        reason, stored_digest = _check_entry(entry, expected_link, algorithm)

        if reason is not None:
            broken = cursor
            break

        expected_link = stored_digest
        last_seq = cursor
        last_digest = stored_digest
        checked_this += 1
        checked_total += 1
        cursor += 1

    base = {
        "found": True,
        "checked": checked_this,
        "checked_total": checked_total,
        "start": start,
        "requested_start": start,
        "requested_end": end,
        "link": range_link,
        "days": _days_in(start, end),
        # 区间的锚点：区间首条的 link 必须等于它的摘要，跨天/分段校验靠它接上
        "anchor": _brief(_records[start - 1]) if start > 0 else None,
        "broken_time": None,
        "reason": None,
        "break": None,
        "impact": None,
    }

    if broken is not None:
        prev_entry = _records[broken - 1] if broken - 1 >= 0 else None
        next_entry = _records[broken]
        base.update({
            "ok": False,
            "end": last_seq,
            "digest": last_digest,
            "broken": broken,
            "broken_time": _field(next_entry, "time"),
            "reason": reason,
            "break": {
                "seq": broken,
                "reason": reason,
                "time": _field(next_entry, "time"),
                "prev": _brief(prev_entry),
                "next": _brief(next_entry),
                "expected_link": expected_link,
                "found_link": _field(next_entry, "link"),
                "stored_digest": _field(next_entry, "digest"),
            },
            "impact": {
                "start": broken,
                "end": len(_records) - 1,
                "count": len(_records) - broken,
                "day_start": _field(next_entry, "day"),
                "day_end": _field(_records[-1], "day"),
                "beyond_range": max(0, len(_records) - 1 - end),
            },
            "resume": None,
            "complete": True,
        })
        return base

    if cursor <= end:
        base.update({
            "ok": True,
            "end": last_seq,
            "digest": last_digest,
            "broken": None,
            "resume": {
                "v": 1,
                "start": start,
                "end": end,
                "next": cursor,
                "link": expected_link,
                "range_link": range_link,
                "checked": checked_total,
                "algorithm": algorithm,
            },
            "complete": False,
        })
        return base

    base.update({
        "ok": True,
        "end": last_seq,
        "digest": last_digest,
        "broken": None,
        "resume": None,
        "complete": True,
    })
    return base


# ---------------------------------------------------------------------------
# 查看
# ---------------------------------------------------------------------------

def tail():
    """取尾部摘要，没有记录时返回空。"""
    if not _records:
        return {"seq": None, "day": "", "time": "", "digest": "", "link": "",
                "algorithm": _DEFAULT_ALGORITHM, "record": None}
    index = len(_records) - 1
    last = _records[-1]
    return {
        "seq": _field(last, "seq", index),
        "day": _field(last, "day"),
        "time": _field(last, "time"),
        "digest": _field(last, "digest"),
        "link": _field(last, "link"),
        "algorithm": _entry_algorithm(last) if isinstance(last, dict) else _DEFAULT_ALGORITHM,
        "record": copy.deepcopy(last.get("record")) if isinstance(last, dict) else None,
    }


def days():
    """按天汇总，保留每天的首尾序号和跨天衔接值 link。"""
    out = []
    for index, entry in enumerate(_records):
        day = _field(entry, "day")
        if out and out[-1]["day"] == day:
            out[-1]["last_seq"] = _field(entry, "seq", index)
            out[-1]["digest"] = _field(entry, "digest")
            out[-1]["time"] = _field(entry, "time")
            out[-1]["count"] += 1
        else:
            out.append({
                "day": day,
                "first_seq": _field(entry, "seq", index),
                "last_seq": _field(entry, "seq", index),
                "link": _field(entry, "link"),
                "digest": _field(entry, "digest"),
                "time": _field(entry, "time"),
                "count": 1,
            })
    return out


def boundary(day):
    """返回某天的分段信息（含进入当天的衔接值），不存在时返回 None。"""
    for item in days():
        if item["day"] == day:
            return item
    return None
