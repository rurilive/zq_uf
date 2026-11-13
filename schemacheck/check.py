"""入库前必填校验的最小实现。"""


def validate(rows, rules):
    passed = []
    rejected = []
    for index, row in enumerate(rows):
        missing = [name for name in rules.get("required", []) if not row.get(name)]
        if missing:
            rejected.append({"row": index, "missing": missing})
        else:
            passed.append(row)
    return {"ok": passed, "bad": rejected}
