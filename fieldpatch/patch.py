"""字段缺失修补，当前原样返回。"""

_stats = {"patched": 0, "isolated": 0}


def patch(row, schema):
    """不补也不隔离。"""
    return {"row": dict(row), "patched": [], "isolated": [], "sources": {}}


def report():
    """统计恒为零。"""
    return dict(_stats)
