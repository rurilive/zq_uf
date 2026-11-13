"""正常与改版两组页面的巡检演示。"""

from selectorwatch import check, reset, set_selectors

NORMAL = [
    {"title": "机械键盘", "price": "299"},
    {"title": "无线鼠标", "price": "99"},
    {"title": "降噪耳机", "price": "199"},
]

REDESIGNED = [
    {"name": "机械键盘", "price": "售价 299 元"},
    {"name": "无线鼠标", "price": "售价 99 元"},
    {"name": "降噪耳机", "price": "售价 199 元"},
]


def run(label, rounds):
    print("==", label, "==")
    emitted = []
    for index, pages in enumerate(rounds, start=1):
        result = check("shop", pages)
        print("第%d轮 hits=%d alerts=%d"
              % (index, result["hits"], len(result["alerts"])))
        for alert in result["alerts"]:
            emitted.append(alert)
            print("  ", alert["level"], alert["field"], alert["selector"],
                  "reasons=", alert["reasons"],
                  "hit_rate=", alert["hit_rate"],
                  "baseline=", alert["baseline"],
                  "samples=", alert["samples"])
    return emitted


reset()
set_selectors({"title": "h1.product-title", "price": "span.price"})

run("正常页面", [NORMAL, NORMAL, NORMAL])
alerts = run("改版页面", [REDESIGNED, REDESIGNED])
print("静默期合并后 repeats=", [alert["repeats"] for alert in alerts])
