"""多源对账，当前只比前两份数据。"""


def reconcile(sources, config=None):
    """取前两份按 key 比较，第三份及以后直接忽略。"""
    key = (config or {}).get("key", "id")
    left, right = sources[0], sources[1]
    left_map = {row[key]: row for row in left}
    right_map = {row[key]: row for row in right}
    diffs = []
    for key_value in sorted(set(left_map) | set(right_map)):
        a = left_map.get(key_value)
        b = right_map.get(key_value)
        if a is None or b is None:
            diffs.append({"key": key_value, "kind": "missing"})
        else:
            changed = [field for field in a if field in b and a[field] != b[field]]
            if changed:
                diffs.append({"key": key_value, "kind": "value", "fields": changed})
    return {"diffs": diffs, "sources": 2}
