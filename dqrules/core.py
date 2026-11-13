"""只校验必填字段的规则执行器。"""


def run(dataset, rules):
    required = rules.get("required", [])
    passed = []
    failed = []
    for index, row in enumerate(dataset):
        missing = [name for name in required if not row.get(name)]
        if missing:
            failed.append({"row": index, "missing": missing})
        else:
            passed.append(row)
    return {"passed": passed, "failed": failed}
