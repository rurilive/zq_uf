"""读取站点配置的 JSON。"""
import json


def load(site):
    with open(site, encoding="utf-8") as handle:
        return json.load(handle)
