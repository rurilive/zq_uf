from ssrfverify import check, report

print(check("http://10.0.0.5/item?id=1", "id", "root:x:0:0"))
print(check("http://10.0.0.5/item?id=1", "id", "root:x:0:0"))
print(check("http://10.0.0.5/item?id=2", "id", "not found"))
print(report())

# TODO: 第二段演示（实现后补）
