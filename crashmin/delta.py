"""崩溃样本最小化：反复删减输入，每一步都验证崩溃仍在。

crash 是内存里的样本，data 是原始内容，trigger 是触发崩溃的片段。
data 中包含 trigger 即视为崩溃复现。删减失败就回退到上一个可用样本，
原始样本和最小样本都保留，复现以最小样本为准。
"""

# 删减比例低于该值时认为输入可能已经接近最小，提示避免继续空跑。
NEAR_MINIMAL_RATIO = 0.1

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


def _command(sample_id):
    return (
        "python3 -c \"from crashmin import reproduce; "
        "print(reproduce('%s'))\"" % sample_id
    )


def _location(sample_id):
    return "crashmin://samples/%s/original" % sample_id


def _minimize_data(data, trigger):
    """贪心删减：按块尝试删除，崩溃仍在才接受，否则回退。

    返回 (最小样本, 步骤记录)。每步记录删减区间和崩溃是否仍在。
    一整轮没有删减成功就把块减半，直到块为 1，避免空跑。
    """
    steps = []
    current = data
    chunk = max(1, len(current) // 2)
    while chunk >= 1 and len(current) > 1:
        reduced = False
        i = 0
        while i < len(current) and len(current) > 1:
            end = min(len(current), i + chunk)
            candidate = current[:i] + current[end:]
            crashes = _crashes(candidate, trigger)
            steps.append({
                "start": i,
                "end": end,
                "crashes": crashes,
                "size": len(candidate),
            })
            if crashes:
                # 崩溃仍在，接受删减；位置不动，继续试同一处。
                current = candidate
                reduced = True
            else:
                # 复现失败，回退到上一个可用样本，跳过该区间。
                i = end
        if not reduced:
            chunk //= 2
    return current, steps


def minimize(crash):
    """最小化崩溃样本，返回大小、删减比例、复现命令和原始样本位置。"""
    _check(crash)
    data = crash["data"]
    trigger = crash["trigger"]
    minimal, steps = _minimize_data(data, trigger)
    ratio = round(1 - len(minimal) / len(data), 3) if data else 0.0
    hint = None
    if ratio < NEAR_MINIMAL_RATIO:
        hint = "删减比例过低，输入可能已经接近最小，继续最小化意义不大"
    _samples[crash["id"]] = {
        "original": data,
        "data": minimal,
        "trigger": trigger,
        "command": _command(crash["id"]),
    }
    return {
        "id": crash["id"],
        "size": len(minimal),
        "ratio": ratio,
        "data": minimal,
        "original": _location(crash["id"]),
        "steps": steps,
        "command": _command(crash["id"]),
        "hint": hint,
    }


def reproduce(sample_id):
    """按编号取回能直接跑的输入；最小样本和原始样本都在时以最小样本为准。"""
    item = _samples.get(sample_id)
    if item is None:
        return {
            "id": sample_id,
            "found": False,
            "data": None,
            "command": None,
        }
    data = item["data"] if item["data"] is not None else item["original"]
    return {
        "id": sample_id,
        "found": True,
        "data": data,
        "crashes": _crashes(data, item["trigger"]),
        "original": _location(sample_id),
        "command": item["command"],
    }
