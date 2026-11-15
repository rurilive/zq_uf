"""字段画像，当前全量统计。"""

_rows = []


def observe(rows):
    """把所有行留给最后一次统计。"""
    _rows.extend(dict(row) for row in rows)
    return {"observed": len(rows), "total": len(_rows)}


def profile(field):
    """每次都对全量数据重算，阈值写死。"""
    values = [row.get(field) for row in _rows]
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    mean = sum(numbers) / len(numbers) if numbers else 0.0
    outliers = [value for value in numbers if value > 100.0]
    return {
        "field": field,
        "count": len(values),
        "mean": mean,
        "outliers": outliers,
        "updated": False,
    }
