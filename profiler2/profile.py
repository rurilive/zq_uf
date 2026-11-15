"""字段画像，当前全量统计。"""

_rows = []


def observe(rows):
    """把所有行留在内存。"""
    _rows.extend(dict(row) for row in rows)
    return {"observed": len(rows), "total": len(_rows)}


def profile(field):
    """每次全量重算，阈值写死。"""
    values = [row.get(field) for row in _rows]
    numbers = [float(v) for v in values if isinstance(v, (int, float))]
    mean = sum(numbers) / len(numbers) if numbers else 0.0
    return {"field": field, "count": len(values), "mean": mean,
            "outliers": [v for v in numbers if v > 100.0], "updated": False}
