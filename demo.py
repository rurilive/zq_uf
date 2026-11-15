from crawlreport import build, reconcile

batch = {"id": "b1", "rows": [{"site": "a", "status": "ok"}, {"site": "a", "status": "failed"}]}
print(build(batch))
print(reconcile(build(batch)))

# TODO: 第二段演示（实现后补）
