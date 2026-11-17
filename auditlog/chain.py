"""链式审计日志：逐条摘要、跨天衔接、区间校验与断点续算。

每条记录保存::

    seq             序号
    day             所属日期（按天切分的边界）
    record          原始记录
    algo            计算本条摘要时使用的算法
    link            链上前一条的摘要；跨天时就是前一天最后一条的摘要
    digest          本条摘要，覆盖 link、seq、day 和 record
    day_entry_link  当天入口的衔接值（当天第一条的 link）

因此改动任意一条记录的内容、摘要或它后面的链接，都会在校验时暴露出来。
按天切分时，每个分段的 exit_digest 就是下一段的入口衔接值。链本身无法
发现尾部被整段删除，需要外部保留区间末尾摘要（verify 的 expect_tail）。
"""

import hashlib
import json

GENESIS = ""
LEGACY_ALGO = "sha256-16"

_records = []
_default_algo = "sha256"
_checks = {}

_TIME_KEYS = ("ts", "time", "timestamp", "at", "created_at")


def _hash_fn(name):
    ctor = {
        "sha256": hashlib.sha256,
        "sha512": hashlib.sha512,
        "sha3_256": hashlib.sha3_256,
        "blake2b": hashlib.blake2b,
    }[name]
    return lambda data, _ctor=ctor: _ctor(data).hexdigest()


_HASHERS = {name: _hash_fn(name) for name in
            ("sha256", "sha512", "sha3_256", "blake2b")}


def _payload(link, seq, day, record):
    return json.dumps(
        {"link": link, "seq": seq, "day": day, "record": record},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _compute_digest(algo, link, seq, day, record):
    """按记录写入时的算法重算摘要。

    兼容旧数据：没有 algo 字段的历史记录使用 LEGACY_ALGO，旧算法只摘要
    记录本身（和改造前的行为一致），所以旧链接仍能验证。
    """
    if algo == LEGACY_ALGO:
        # 旧实现：json.dumps 默认分隔符 + sha256 前 16 位
        payload = json.dumps(record, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    fn = _HASHERS.get(algo)
    if fn is None:
        raise ValueError("unknown digest algorithm: %r" % (algo,))
    return fn(_payload(link, seq, day, record).encode("utf-8"))


def _entry_algo(item):
    if not isinstance(item, dict):
        return ""
    algo = item.get("algo")
    if isinstance(algo, str) and algo:
        return algo
    # 旧快照没有 algo/link，摘要长度 16；新记录默认 sha256 全 64 位。
    if len(str(item.get("digest", ""))) == 16:
        return LEGACY_ALGO
    return "sha256"


def _item_day(item):
    return item.get("day", "") if isinstance(item, dict) else ""


def _record_time(item):
    """取记录时间：优先记录里的 ts/time 等字段，否则退回 day。"""
    if not isinstance(item, dict):
        return ""
    record = item.get("record")
    if isinstance(record, dict):
        for key in _TIME_KEYS:
            value = record.get(key)
            if isinstance(value, str) and value:
                return value
            if value is not None and not isinstance(value, (dict, list)):
                return str(value)
    day = item.get("day", "")
    return day if isinstance(day, str) else str(day)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _split_stamp(value):
    """把 '2026-01-02' 或 '2026-01-02T09:00:00' 拆成 (day, time)。

    time 为 None 表示只按天定位。
    """
    text = str(value)
    if len(text) > 10 and text[4] == "-" and text[7] == "-":
        return text[:10], text
    return text, None


def _boundary(day, time_value, first):
    """按日期（可选再按时间）找区间边界。

    返回第一条命中的 seq（first=True）或最后一条命中的 seq（first=False）；
    没有命中时返回空区间的标记：起点为 len(_records)，终点为 -1。
    只给日期时包含当天全部记录；给了完整时间戳时同一天内再按时间收窄。
    """
    size = len(_records)
    if day is None:
        # 只给了时间：直接和记录时间比较
        key = str(time_value)
        if first:
            for index, item in enumerate(_records):
                if _record_time(item) >= key:
                    return index
            return size
        end = -1
        for index, item in enumerate(_records):
            if _record_time(item) <= key:
                end = index
        return end

    key_day = str(day)
    key_time = None if time_value is None else str(time_value)

    if first:
        for index, item in enumerate(_records):
            item_day = _item_day(item)
            if item_day > key_day:
                return index
            if item_day == key_day and (key_time is None
                                        or _record_time(item) >= key_time):
                return index
        return size

    end = -1
    for index, item in enumerate(_records):
        item_day = _item_day(item)
        if item_day < key_day or (item_day == key_day and (
                key_time is None or _record_time(item) <= key_time)):
            end = index
    return end


def _resolve_span(span):
    """把区间写法统一成闭区间 (start, end)。

    支持 None、单条 seq、range、[start, end]、(start, end)、按天字符串，
    以及 {"start"/"end"}、{"from_day"/"to_day"}、{"from_time"/"to_time"}。
    """
    size = len(_records)
    if span is None:
        return (0, size - 1) if size else (0, -1)

    if _is_int(span):
        return int(span), int(span)

    if isinstance(span, str):
        # 日期按天切，完整时间戳再收窄到该时刻
        day, stamp = _split_stamp(span)
        return (_boundary(day, stamp, True), _boundary(day, stamp, False))

    if isinstance(span, range):
        if len(span) == 0:
            return int(span.start), int(span.start) - 1
        return int(span[0]), int(span[-1])

    if isinstance(span, (tuple, list)):
        if len(span) != 2:
            raise ValueError("range must have exactly two elements")
        start, end = span
        if isinstance(start, str):
            start = _boundary(*_split_stamp(start), first=True)
        if isinstance(end, str):
            end = _boundary(*_split_stamp(end), first=False)
        if not _is_int(start) or not _is_int(end):
            raise ValueError("range boundaries must be integers")
        return int(start), int(end)

    if isinstance(span, dict):
        start = span.get("start", span.get("from"))
        end = span.get("end", span.get("to"))
        from_day = span.get("from_day")
        to_day = span.get("to_day")
        from_time = span.get("from_time")
        to_time = span.get("to_time")
        from_stamp = span.get("from_stamp")
        to_stamp = span.get("to_stamp")

        # 字典里直接写日期/时间字符串时，按天或按时刻处理
        if isinstance(start, str) and from_day is None and from_time is None:
            from_day, from_time = _split_stamp(start)
            start = None
        if isinstance(end, str) and to_day is None and to_time is None:
            to_day, to_time = _split_stamp(end)
            end = None

        if start is None and (from_day is not None or from_time is not None
                              or from_stamp is not None):
            if from_stamp is not None:
                from_day, from_time = _split_stamp(from_stamp)
            start = _boundary(from_day, from_time, True)
        if end is None and (to_day is not None or to_time is not None
                            or to_stamp is not None):
            if to_stamp is not None:
                to_day, to_time = _split_stamp(to_stamp)
            end = _boundary(to_day, to_time, False)

        if start is None:
            start = 0
        if end is None:
            end = size - 1
        if not _is_int(start) or not _is_int(end):
            raise ValueError("range boundaries must be integers")
        return int(start), int(end)

    raise ValueError("span must be None, int, str, range, tuple/list or dict")


def _neighbor(seq):
    if not (0 <= seq < len(_records)):
        return None
    item = _records[seq]
    if not isinstance(item, dict):
        return {"seq": seq, "day": "", "time": "", "record": item,
                "digest": "", "link": "", "algo": ""}
    return {
        "seq": seq,
        "day": item.get("day", ""),
        "time": _record_time(item),
        "record": item.get("record"),
        "digest": item.get("digest", ""),
        "link": item.get("link", ""),
        "algo": _entry_algo(item),
    }


def _detail(seq, reason, expected_link=None, actual_link=None,
            computed_digest=None, stored_digest=None, extra=None):
    item = _records[seq] if 0 <= seq < len(_records) else None
    if isinstance(item, dict):
        day = item.get("day", "")
        record = item.get("record")
    else:
        day = ""
        record = item
    detail = {
        "seq": seq,
        "reason": reason,
        "day": day,
        "time": _record_time(item),
        "record": record,
        "prev": _neighbor(seq - 1),
        "next": _neighbor(seq + 1),
        "expected_link": expected_link,
        "actual_link": actual_link,
        "computed_digest": computed_digest,
        "stored_digest": stored_digest,
    }
    if extra:
        detail.update(extra)
    return detail


def _impact(seq):
    """断点之后整条链都不再可信，给出影响到的全局范围。"""
    size = len(_records)
    if size == 0:
        return None
    days = []
    for item in _records[seq:]:
        day = _item_day(item)
        if day not in days:
            days.append(day)
    return {
        "start": seq,
        "end": size - 1,
        "count": size - seq,
        "records": size - seq,
        "days": days,
    }


def _days_between(start, end):
    """校验区间覆盖的日切分段，带每段的跨天衔接值。"""
    if start is None or end is None or start > end:
        return []
    result = []
    for index in range(start, end + 1):
        item = _records[index]
        day = _item_day(item)
        digest = item.get("digest", "") if isinstance(item, dict) else ""
        if result and result[-1]["day"] == day:
            part = result[-1]
            part["end"] = index
            part["count"] += 1
            part["exit_digest"] = digest
        else:
            if isinstance(item, dict):
                entry = item.get("day_entry_link", item.get("link", ""))
            else:
                entry = ""
            result.append({
                "day": day,
                "start": index,
                "end": index,
                "count": 1,
                "entry_link": entry,
                "exit_digest": digest,
                "prev_day": result[-1]["day"] if result else None,
            })
    return result


def _empty_result(range_start, range_end, anchor):
    return {
        "ok": True,
        "done": True,
        "checked": 0,
        "start": None,
        "end": None,
        "range": {"start": range_start, "end": range_end},
        "checked_range": {"start": None, "end": None},
        "anchor": anchor if anchor is not None else GENESIS,
        "anchor_source": "external" if anchor is not None else "unchecked",
        "link": anchor if anchor is not None else GENESIS,
        "digest": None,
        "resume": None,
        "broken": None,
        "broken_detail": None,
        "broken_range": None,
        "impact": None,
        "algos": [],
        "days": [],
    }


def _broken_result(detail, range_start, range_end, anchor):
    """整个区间都读不到记录、但仍能从外部摘要判断被删时的结果。"""
    seq = detail["seq"]
    result = {
        "ok": False,
        "done": True,
        "checked": 0,
        "start": None,
        "end": None,
        "range": {"start": range_start, "end": range_end},
        "checked_range": {"start": None, "end": None},
        "anchor": anchor if anchor is not None else GENESIS,
        "anchor_source": "external" if anchor is not None else "unchecked",
        "link": anchor if anchor is not None else GENESIS,
        "digest": None,
        "resume": None,
        "broken": seq,
        "broken_detail": detail,
        "broken_range": {"start": seq, "end": seq},
        "impact": {
            "start": seq,
            "end": None,
            "count": None,
            "records": None,
            "days": [],
            "kind": "truncated",
        },
        "algos": [],
        "days": [],
    }
    _remember(result)
    return result


def _remember(result):
    # 空区间不算"校验过"；但断链结果即使一条都没读到也要留档，
    # 否则整段被删的情况会从重跑记录里消失。
    if not result["done"]:
        return
    if not result["checked"] and result["broken"] is None:
        return
    key = (result["range"]["start"], result["range"]["end"], result["anchor"])
    _checks[key] = {
        "range": dict(result["range"]),
        "checked_range": dict(result["checked_range"]),
        "anchor": result["anchor"],
        "anchor_source": result["anchor_source"],
        "ok": result["ok"],
        "done": result["done"],
        "checked": result["checked"],
        "start": result["start"],
        "end": result["end"],
        "broken": result["broken"],
        "digest": result["digest"],
        "algos": list(result["algos"]),
    }


def append(record, algo=None):
    """追加一条记录，返回摘要值、衔接值和日切信息。"""
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    algo = _default_algo if algo is None else algo
    if not isinstance(algo, str) or algo not in _HASHERS:
        raise ValueError("unknown digest algorithm: %r" % (algo,))

    seq = len(_records)
    day = record.get("day", "")
    if not isinstance(day, str):
        day = str(day)
    link = _records[-1].get("digest", "") if _records else GENESIS
    digest = _compute_digest(algo, link, seq, day, record)

    day_start = not _records or _item_day(_records[-1]) != day
    if day_start:
        day_entry_link = link
    else:
        day_entry_link = _records[-1].get("day_entry_link", link)

    item = {
        "seq": seq,
        "day": day,
        "record": dict(record),
        "algo": algo,
        "link": link,
        "digest": digest,
        "day_entry_link": day_entry_link,
    }
    _records.append(item)
    return {
        "seq": seq,
        "day": day,
        "time": _record_time(item),
        "algo": algo,
        "digest": digest,
        "link": link,
        "day_start": day_start,
        "day_entry_link": day_entry_link,
    }


def _truncated_detail(expected, actual, seq):
    """区间末尾摘要对不上：尾部记录被删或被换。"""
    return {
        "seq": seq,
        "reason": "truncated",
        "day": "",
        "time": "",
        "record": None,
        "prev": _neighbor(seq - 1),
        "next": None,
        "expected_link": None,
        "actual_link": None,
        "computed_digest": actual,
        "stored_digest": expected,
        "expected_digest": expected,
        "actual_digest": actual,
    }


def verify(span=None, limit=None, resume=None, anchor=None, expect_tail=None):
    """校验指定区间，支持分块中断与断点续算。

    span 可以是 seq 闭区间、单条 seq、range、按天字符串，或带
    from_day/to_day、from_time/to_time 的字典。limit 限制本次校验条数，
    返回的 resume 可直接作为下一次调用的 resume 参数。

    expect_tail 是从外部保存下来的区间末尾摘要；哈希链本身无法发现
    尾部被删，带上它才能发现截断。
    """
    if isinstance(span, dict):
        if limit is None:
            limit = span.get("limit")
        if resume is None:
            resume = span.get("resume")
        if anchor is None:
            anchor = span.get("anchor")
        if expect_tail is None:
            expect_tail = span.get("expect_tail")

    if limit is not None:
        if not _is_int(limit) or int(limit) < 1:
            raise ValueError("limit must be a positive integer")
        limit = int(limit)

    range_start, range_end = _resolve_span(span)

    if resume is not None:
        if not _is_int(resume):
            raise ValueError("resume must be an integer")
        start = int(resume)
    else:
        start = range_start

    size = len(_records)
    start = max(0, start)
    end = min(size - 1, range_end)
    if start > end:
        # 区间里一条记录都没有。如果外部还留着这个区间的末尾摘要，
        # 说明整段被删掉了。
        if expect_tail is not None and range_start <= range_end:
            detail = _truncated_detail(expect_tail, None, range_start)
            return _broken_result(detail, range_start, range_end, anchor)
        return _empty_result(range_start, range_end, anchor)

    if anchor is not None:
        expected = anchor
        anchor_source = "external"
    elif start == 0:
        expected = GENESIS
        anchor_source = "genesis"
    else:
        previous = _records[start - 1]
        expected = (
            previous.get("digest", "") if isinstance(previous, dict) else ""
        )
        anchor_source = "previous_digest"
    initial_anchor = expected

    checked = 0
    checked_start = None
    checked_end = None
    last_good = None
    last_good_seq = None
    algos = []
    broken = None
    seq = start

    while seq <= end:
        if limit is not None and checked >= limit:
            break

        item = _records[seq]
        checked += 1
        if checked_start is None:
            checked_start = seq
        checked_end = seq

        if not isinstance(item, dict):
            broken = _detail(seq, "malformed")
            break

        algo = _entry_algo(item)
        if algo and algo not in algos:
            algos.append(algo)

        if "seq" in item and item.get("seq") != seq:
            broken = _detail(
                seq, "seq_mismatch",
                extra={"expected_seq": seq, "actual_seq": item.get("seq")},
            )
            break

        actual_link = item.get("link", "")
        # 改造前的历史记录根本没有 link 字段，按旧方式只能校验自身摘要，
        # 不能要求它衔接前一条；新记录缺少 link 则按断链处理。
        legacy_no_link = "link" not in item and algo == LEGACY_ALGO
        if not legacy_no_link and actual_link != expected:
            broken = _detail(
                seq, "link_mismatch",
                expected_link=expected,
                actual_link=actual_link,
                stored_digest=item.get("digest", ""),
            )
            break

        day = item.get("day", "")
        if not isinstance(day, str):
            day = str(day)

        # 跨天衔接值不参与摘要，但它是可推导的：当天第一条的
        # day_entry_link 必须等于它的 link，当天其余记录必须与当天
        # 第一条保持一致。改掉它同样算断链。
        if "day_entry_link" in item and seq > 0:
            entry = item.get("day_entry_link", "")
            previous = _records[seq - 1]
            same_day = isinstance(previous, dict) \
                and _item_day(previous) == day
            if not same_day:
                expected_entry = actual_link
            elif "day_entry_link" in previous:
                expected_entry = previous.get("day_entry_link", "")
            else:
                # 同一天里混有改造前的旧记录，没有衔接值可比，跳过
                expected_entry = entry
            if entry != expected_entry:
                broken = _detail(
                    seq, "day_link_mismatch",
                    expected_link=expected_entry,
                    actual_link=entry,
                    stored_digest=item.get("digest", ""),
                )
                break

        stored = item.get("digest", "")
        try:
            computed = _compute_digest(
                algo, actual_link, seq, day, item.get("record")
            )
        except (ValueError, TypeError) as exc:
            broken = _detail(
                seq, "unverifiable",
                expected_link=expected,
                actual_link=actual_link,
                stored_digest=stored,
                extra={"error": str(exc)},
            )
            break

        if computed != stored:
            broken = _detail(
                seq, "digest_mismatch",
                expected_link=expected,
                actual_link=actual_link,
                computed_digest=computed,
                stored_digest=stored,
            )
            break

        last_good = stored
        last_good_seq = seq
        expected = stored
        seq += 1

    # 区间最后一条记录被删掉时，剩下的记录仍然自洽，只能靠外面保存的
    # 末尾摘要（expect_tail）发现：摘要对不上就说明末尾少了记录。
    # 区间末尾的记录被删或被换掉时，剩下的记录仍然自洽，只能靠外面保存的
    # 末尾摘要（expect_tail）发现。
    if broken is None and expect_tail is not None and seq > end \
            and last_good_seq == end and last_good != expect_tail:
        missing_at = range_end if end == range_end else end + 1
        broken = _truncated_detail(expect_tail, last_good, missing_at)

    if broken is not None:
        done = True
        next_resume = None
        broken_seq = broken["seq"]
        broken_range = {"start": broken_seq, "end": broken_seq}
        if broken["reason"] == "truncated":
            # 尾部之后还少了多少条，只能由外部锚点判断，这里只给出起点。
            impact = {
                "start": broken_seq,
                "end": None,
                "count": None,
                "records": None,
                "days": [],
                "kind": "truncated",
            }
        else:
            impact = _impact(min(broken_seq, len(_records) - 1))
    else:
        broken_seq = None
        broken_range = None
        impact = None
        if seq <= end:
            done = False
            next_resume = seq
        else:
            done = True
            next_resume = None

    result = {
        "ok": broken is None,
        "done": done,
        "checked": checked,
        "start": checked_start,
        "end": checked_end,
        "range": {"start": range_start, "end": range_end},
        "checked_range": {"start": checked_start, "end": checked_end},
        "anchor": initial_anchor,
        "anchor_source": anchor_source,
        "link": last_good if last_good is not None else initial_anchor,
        "digest": last_good,
        "resume": next_resume,
        "broken": broken_seq,
        "broken_detail": broken,
        "broken_range": broken_range,
        "impact": impact,
        "algos": sorted(algos),
        "days": _days_between(checked_start, checked_end),
    }
    _remember(result)
    return result


def tail():
    """取尾部摘要，没有记录时返回空。"""
    if not _records:
        return {"seq": None, "day": "", "digest": "", "link": "",
                "algo": "", "time": ""}
    last = _records[-1]
    return {
        "seq": last.get("seq"),
        "day": last.get("day", ""),
        "digest": last.get("digest", ""),
        "link": last.get("link", ""),
        "algo": _entry_algo(last),
        "time": _record_time(last),
    }


def days():
    """按天列出分段：起止 seq、跨天入口链接和当天出口摘要。"""
    result = []
    for index, item in enumerate(_records):
        day = _item_day(item)
        digest = item.get("digest", "") if isinstance(item, dict) else ""
        algo = _entry_algo(item)
        if result and result[-1]["day"] == day:
            part = result[-1]
            part["end"] = index
            part["count"] += 1
            part["exit_digest"] = digest
            if algo and algo not in part["algos"]:
                part["algos"].append(algo)
        else:
            if isinstance(item, dict):
                entry = item.get("day_entry_link", item.get("link", ""))
            else:
                entry = ""
            result.append({
                "day": day,
                "start": index,
                "end": index,
                "count": 1,
                "entry_link": entry,
                "exit_digest": digest,
                "prev_day": result[-1]["day"] if result else None,
                "algos": [algo] if algo else [],
            })
    return result


def set_algo(name):
    """切换后续 append 使用的摘要算法，已写入记录仍按各自算法验证。"""
    if not isinstance(name, str) or name not in _HASHERS:
        raise ValueError("unknown digest algorithm: %r" % (name,))
    global _default_algo
    _default_algo = name
    return name


def register_algo(name, fn):
    """注册自定义摘要算法：fn(bytes) -> 十六进制摘要字符串。"""
    if not isinstance(name, str) or not name:
        raise ValueError("algorithm name must be a non-empty string")
    if name == LEGACY_ALGO or name in _HASHERS:
        raise ValueError("algorithm already reserved: %r" % (name,))
    if not callable(fn):
        raise ValueError("algorithm must be callable")
    sample = fn(b"")
    if hasattr(sample, "hexdigest"):
        wrapped = lambda data, _fn=fn: _fn(data).hexdigest()
    elif hasattr(sample, "hex"):
        wrapped = lambda data, _fn=fn: _fn(data).hex()
    elif isinstance(sample, str) and sample:
        wrapped = fn
    else:
        raise ValueError("algorithm must return a hex digest string")
    _HASHERS[name] = wrapped
    return name


def current_algo():
    return _default_algo


def checked_ranges():
    """已完成的区间校验结果，可据此单独重跑历史区间。"""
    return [
        dict(value, range=dict(value["range"]), algos=list(value["algos"]))
        for _, value in sorted(_checks.items())
    ]


def verify_day(day, entry_link=None, limit=None, resume=None,
               expect_tail=None):
    """只校验某一天的分段，不必从第一天重算。

    默认用前一天最后一条的摘要作为锚点，只校验当天分段。entry_link 可传
    外部可信来源里的衔接值（例如前一天导出时保存的出口摘要），这样连
    前一天的记录都不必读。expect_tail 传当天最后一条的摘要，可发现当天
    尾部记录被删。
    """
    parts = [part for part in days() if part["day"] == day]
    if not parts:
        return _empty_result(0, -1, entry_link)
    part = parts[0]
    return verify(
        (part["start"], part["end"]),
        limit=limit,
        resume=resume,
        anchor=entry_link,  # None 时按前一条摘要校验，只保证段内自洽
        expect_tail=expect_tail,
    )


def rerun(span=None):
    """单独重跑历史区间；不传 span 时逐个重跑已校验过的区间。"""
    if span is not None:
        return verify(span)
    results = []
    for start, end, anchor in sorted(_checks):
        # 重跑时带上当初的锚点，避免上一条记录被改后按新摘要"自证清白"
        results.append(verify((start, end), anchor=anchor))
    return results


def reset():
    """清空日志与校验记录（测试和演示用）。"""
    global _default_algo
    _records.clear()
    _checks.clear()
    _default_algo = "sha256"
