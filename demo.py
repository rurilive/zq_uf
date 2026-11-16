from xssreflect import PAYLOAD, check, load, report

print("页面大小", load("search", "<div>%s</div>" % PAYLOAD)["size"])
out = check("search", "q")
print("命中位置", [item["offset"] for item in out["findings"]])
print("上下文", out["findings"][0]["context"])
print("命中条数", report()["hits"], "未覆盖", report()["uncovered"])

# TODO: 第二段演示（实现后补）
