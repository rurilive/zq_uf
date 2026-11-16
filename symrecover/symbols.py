"""二进制符号恢复，当前按地址硬编码名字。"""

_registry = {}

_ADDRESS_PREFIX = "sub_"


def _entry(func):
    address = str(func.get("address", ""))
    return {
        "address": address,
        "name": _ADDRESS_PREFIX + address,
        "evidence": "",
        "confidence": "",
        "stable_id": "",
    }


def recover(binary):
    """只把地址拼成名字，不看调用关系和字符串。"""
    if not isinstance(binary, dict):
        raise ValueError("binary must be a dict")
    functions = binary.get("functions")
    if not isinstance(functions, list):
        raise ValueError("functions must be a list")
    name = binary.get("name", "")
    entries = [_entry(func) for func in functions]
    _registry[name] = {
        "version": binary.get("version", ""),
        "entries": entries,
    }
    return {
        "binary": name,
        "recovered": len(entries),
        "names": entries,
        "unmatched": [],
    }


def lookup(binary, address):
    """按地址取一条，取不到返回空字典。"""
    record = _registry.get(binary)
    if record is None:
        return {}
    for item in record["entries"]:
        if item["address"] == str(address):
            return dict(item)
    return {}


def report():
    """汇总恢复数量，依据和可信度都是空的。"""
    total = 0
    for record in _registry.values():
        total += len(record["entries"])
    return {
        "binaries": len(_registry),
        "recovered": total,
        "confidence": {},
        "evidence": [],
        "unmatched": [],
    }
