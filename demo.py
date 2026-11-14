from jsonshape import MIN_BATCHES, MIN_PAGES, observe, report, reset

reset()

normal_shop = {"id": 1, "price": 2, "items": [{"sku": "a"}]}
normal_news = {"id": 1, "title": "t", "tags": ["x", "y"]}


def feed(site, payload):
    for _ in range(MIN_BATCHES):
        observe(site, [dict(payload) for _ in range(MIN_PAGES)])


print("== 正常响应：两个站点各自学习基线 ==")
feed("shop", normal_shop)
feed("news", normal_news)
print(observe("shop", normal_shop))
print(observe("news", normal_news))

print("\n== 改版响应：shop 字段变类型并新增字段，news 不受影响 ==")
changed_shop = {"id": "sku-1", "price": 2, "items": [{"sku": "a"}], "stock": 0}
for index in range(3):
    result = observe("shop", changed_shop)
    print(f"shop 第{index + 1}批异常:", result["severity"],
          [a["kind"] + "@" + a["path"] for a in result["alerts"]])
print("news 同期巡检:", observe("news", normal_news)["alert"])

print("\n== report ==")
for alert in report("shop")["alerts"][:2]:
    print(alert["severity"], alert["kind"], alert["path"],
          "baseline:", alert["baseline"], "current:", alert["current"],
          "samples:", alert["sample_pages"])
print("news 告警数:", len(report("news")["alerts"]))
