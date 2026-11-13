from proxycheck import check, pick

SCORES = {"p1": (False, 900), "p2": (True, 120), "p3": (True, 800)}


def probe(proxy):
    return SCORES[proxy]


results = check(["p1", "p2", "p3"], probe)
for item in results:
    print(item["proxy"], item["ok"], item["latency"])
print("选中", pick(results))

# TODO: 第二段演示（实现后补）
