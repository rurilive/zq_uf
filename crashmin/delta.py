"""崩溃样本最小化：逐步删减输入，每一步都验证崩溃仍可复现。

crash 是内存里的样本，data 是原始内容，trigger 是触发崩溃的片段。
data 中仍包含 trigger 即视为崩溃仍可复现；删减导致复现失败时回退到
上一个可用样本。原始样本和最小样本都保留，复现以最小样本为准。
"""

# 单轮删减比例低于此值时认为输入已接近最小，提示并停止，避免继续空跑。
MIN_GAIN = 0.01

_samples = {}


def _check(crash):
    if not isinstance(crash, dict):
        raise ValueError("crash must be a dict")
    if not crash.get("id"):
        raise ValueError("crash needs id")
    if not isinstance(crash.get("data"), str):
        raise ValueError("crash needs str data")
    if not isinstance(crash.get("trigger"), str) or not crash["trigger"]:
        raise ValueError("crash needs str trigger")


def _crashes(data, trigger):
    return trigger in data


def _location(sample_id, kind):
    return "samples/{}/{}".format(sample_id, kind)


def _command(sample_id):
    return "python3 -c \"from crashmin import reproduce; reproduce('{}')\"".format(sample_id)


def _pass(current, trigger, size, steps):
    """按 size 的区间扫一遍 current，删掉删了仍崩溃的区间，返回是否删掉了内容。"""
    removed = False
    i = 0
    while i < len(current):
        end = min(i + size, len(current))
        candidate = current[:i] + current[end:]
        crashed = bool(candidate) and _crashes(candidate, trigger)
        steps.append({
            "start": i,
            "end": end,
            "crashed": crashed,
            "size_after": len(candidate) if crashed else len(current),
        })
        if crashed:
            current = candidate  # 复现仍在，接受删减；复现失败则回退（不赋值）
            removed = True
        else:
            i = end
    return current, removed


def minimize(crash):
    """反复删减输入直到再删就不崩，返回最小样本、删减比例和复现命令。"""
    _check(crash)
    data = crash["data"]
    trigger = crash["trigger"]
    if not _crashes(data, trigger):
        raise ValueError("trigger does not reproduce on original data")

    current = data
    steps = []
    hint = None
    while len(current) > len(trigger):
        before = len(current)
        size = max(1, before // 2)
        while size >= 1:
            current, removed = _pass(current, trigger, size, steps)
            if removed:
                break  # 有进展就回到大区间重新扫
            size //= 2
        if len(current) == before:
            break  # 删到 1 字节区间都无法再删，已最小
        if 1 - len(current) / before < MIN_GAIN:
            hint = "删减比例过低，输入可能已经接近最小"
            break

    sample_id = crash["id"]
    command = _command(sample_id)
    _samples[sample_id] = {
        "original": data,
        "minimal": current,
        "command": command,
        "steps": steps,
    }
    return {
        "id": sample_id,
        "size": len(current),
        "original_size": len(data),
        "ratio": round(1 - len(current) / len(data), 3),
        "data": current,
        "original": _location(sample_id, "original"),
        "command": command,
        "steps": steps,
        "hint": hint,
    }


def reproduce(sample_id):
    """按编号取能直接跑的输入；最小样本和原始样本都在时以最小样本为准。"""
    item = _samples.get(sample_id)
    if item is None:
        return {
            "id": sample_id,
            "found": False,
            "data": None,
            "command": None,
            "original": None,
        }
    data = item["minimal"] if item["minimal"] is not None else item["original"]
    return {
        "id": sample_id,
        "found": True,
        "data": data,
        "command": item["command"],
        "original": _location(sample_id, "original"),
    }
