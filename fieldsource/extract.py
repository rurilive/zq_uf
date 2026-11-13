"""采集字段提取，当前只保留最终值。"""


def extract(page, rules):
    """按规则顺序取值，后面的命中覆盖前面。"""
    values = {}
    for rule in rules:
        value = rule["get"](page)
        if value is not None and value != "":
            values[rule["field"]] = value
    return values
