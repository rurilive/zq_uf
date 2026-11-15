from detectsoft import judge, report

print(judge({"url": "https://shop/1", "site": "shop", "status": 200, "text": ""}))
print(judge({"url": "https://shop/2", "site": "shop", "status": 200, "text": "暂无内容"}))
print(report("shop"))

# TODO: 第二段演示（实现后补）
