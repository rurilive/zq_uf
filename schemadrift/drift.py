"""结构漂移，当前只看列名。"""


def snapshot(table):
    """记录列名集合。"""
    return {"table": table, "columns": sorted(table.get("columns") or [])}


def diff(base, target):
    """只比较列名增删。"""
    return {
        "added": sorted(set(target["columns"]) - set(base["columns"])),
        "removed": sorted(set(base["columns"]) - set(target["columns"])),
    }
