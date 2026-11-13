"""按主键取最新维表关联。"""


def join(facts, dims):
    latest = {row["id"]: row for row in dims}
    out = []
    for fact in facts:
        dim = latest.get(fact.get("id"))
        out.append({**fact, "dim": dim})
    return out
