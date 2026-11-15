from coldhot import archive, query

print(archive("orders", {"start": "year1", "end": "year2"}))
print(query("orders", {"status": "paid"}))

# TODO: 第二段演示（实现后补）
