"""按正则配置抽取字段的最小实现。"""
import json
import re


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def apply(text, conf):
    result = {}
    for field, pattern in conf.items():
        match = re.search(pattern, text)
        result[field] = match.group(1) if match else ""
    return result
