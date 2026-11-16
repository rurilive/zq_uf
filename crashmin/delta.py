"""崩溃样本最小化，当前只把输入截短到四分之一。

crash 是内存里的样本，data 是原始内容，trigger 是触发崩溃的片段。
"""

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


def minimize(crash):
    """按固定比例截断，截完不再验证是否还能崩溃。"""
    _check(crash)
    data = crash["data"]
    keep = max(1, len(data) // 4)
    current = data[:keep]
    _samples[crash["id"]] = {"data": current}
    return {
        "id": crash["id"],
        "size": len(current),
        "ratio": round(1 - len(current) / len(data), 3),
        "data": current,
        "original": None,
        "steps": 0,
        "command": None,
    }


def reproduce(sample_id):
    """只用截断后的内容，没有原始样本也没有复现命令。"""
    item = _samples.get(sample_id)
    if item is None:
        return {
            "id": sample_id,
            "found": False,
            "data": None,
            "command": None,
        }
    return {
        "id": sample_id,
        "found": True,
        "data": item["data"],
        "command": None,
    }
