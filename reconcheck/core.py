"""只比行数和主键集合的对账。"""


def compare(left, right, config=None):
    left_keys = {row.get("id") for row in left}
    right_keys = {row.get("id") for row in right}
    return {
        "left_only": sorted(left_keys - right_keys),
        "right_only": sorted(right_keys - left_keys),
        "rows_left": len(left),
        "rows_right": len(right),
    }
