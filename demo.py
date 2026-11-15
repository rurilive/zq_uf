from proxysettle import charge, settle

print(charge("proxy-a", 1.5))
print(charge("proxy-a", 2.0))
print(settle("proxy-a"))

# TODO: 第二段演示（实现后补）
