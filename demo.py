from changedetect import diff, should_ingest

d = diff("<p>price 10</p>", "<p>price 10</p><div>ad</div>")
print(d, should_ingest(d))

# TODO: 第二段演示（实现后补）
