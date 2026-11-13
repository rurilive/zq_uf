"""只做字段重命名的转换层。"""


def transform(rows, plan):
    mapping = plan.get("rename", {})
    out = []
    for row in rows:
        out.append({mapping.get(key, key): value for key, value in row.items()})
    return out
