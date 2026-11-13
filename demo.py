"""分叉流程演示：list -> detail -> {clean, enrich} -> store，按站点覆盖参数。

运行：python3 demo.py
输出一行确定的 JSON，tests/test_dag.py 会逐字节比对该输出。
"""

import json

from dagcrawl import for_site, register, run


@register("fetch_list")
def fetch_list(ctx):
    limit = ctx["params"]["limit"]
    return {**ctx, "items": ["item-%d" % i for i in range(limit)]}


@register("fetch_detail")
def fetch_detail(ctx):
    return {**ctx, "details": {item: len(item) for item in ctx["items"]}}


@register("clean")
def clean(ctx):
    min_len = ctx["params"]["min_len"]
    return {**ctx, "cleaned": [item for item in ctx["items"] if len(item) >= min_len]}


@register("enrich")
def enrich(ctx):
    return {**ctx, "enriched": [item.upper() for item in ctx["items"]]}


@register("store")
def store(ctx):
    rows = sorted(set(ctx["cleaned"]) | set(ctx["enriched"]))
    return {**ctx, "stored": rows}


FLOW = {
    "name": "dagcrawl-demo",
    "params": {"limit": 3, "min_len": 6},
    "sites": {
        "site_a": {},
        "site_b": {"limit": 2, "min_len": 7},
    },
    "steps": [
        {"name": "list", "fn": "fetch_list", "provides": ["items"]},
        {"name": "detail", "fn": "fetch_detail", "needs": ["list"],
         "provides": ["details"]},
        {"name": "clean", "fn": "clean", "needs": ["detail"],
         "provides": ["cleaned"]},
        {"name": "enrich", "fn": "enrich", "needs": ["detail"],
         "provides": ["enriched"]},
        {"name": "store", "fn": "store", "needs": ["clean", "enrich"],
         "provides": ["stored"]},
    ],
}


def main():
    flow = for_site(FLOW, "site_b")
    ctx = run(flow, {})
    out = {"params": ctx["params"], "state": flow["state"], "stored": ctx["stored"]}
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()

