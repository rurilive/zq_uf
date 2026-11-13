from crawlmetrics import Stats

stats = Stats()
stats.inc("request_success", 10)
stats.inc("request_fail", 2)
stats.inc("parse_fail", 1)
print(stats.snapshot())

# TODO: 第二段演示（实现后补）
