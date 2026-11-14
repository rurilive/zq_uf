from jsonshape import observe, report, reset


def show(title, result):
    print(title, "=> alert=", result["alert"], "severity=", result["severity"])
    for alert in result["alerts"]:
        print("    -", alert["kind"], alert["path"],
              "baseline=", alert["baseline"],
              "current=", alert["current"],
              "page=", alert["sample_page"])


reset()

print("== 正常响应：冷启动学习，之后稳定不告警 ==")
normal_pages = [
    {"id": 1, "title": "phone", "price": 3999, "tags": ["sale"],
     "seller": {"name": "a"}},
    {"id": 2, "title": "case", "price": 29, "tags": ["new", "hot"],
     "seller": {"name": "b"}},
    {"id": 3, "title": "cable", "price": 49, "tags": [],
     "seller": {"name": "c"}},
    {"id": 4, "title": "film", "price": 19, "tags": ["sale"],
     "seller": {"name": "d"}},
]
for index, payload in enumerate(normal_pages):
    show("page%d" % index, observe("shop", payload))

print()
print("== 改版响应：price 变字符串、seller.name 消失、新增 coupon ==")
revised_pages = [
    {"id": 5, "title": "phone2", "price": "3999", "tags": ["sale"],
     "seller": {}, "coupon": "减10"},
    {"id": 6, "title": "case2", "price": "29", "tags": ["new"],
     "seller": {}, "coupon": "减5"},
    {"id": 7, "title": "cable2", "price": "49", "tags": [],
     "seller": {}, "coupon": "免邮"},
]
for index, payload in enumerate(revised_pages):
    show("revised%d" % index, observe("shop", payload))

print()
print("== 站点基线隔离：news 冷启动不受 shop 告警影响 ==")
print(observe("news", {"id": 1, "headline": "t"}))
print(observe("news", {"id": 2, "headline": "t2"}))
print(observe("news", {"id": 3, "headline": "t3"}))
print("news report:", report("news"))
print("shop critical 条数:",
      sum(1 for alert in report("shop")["alerts"]
          if alert["severity"] == "critical"))
