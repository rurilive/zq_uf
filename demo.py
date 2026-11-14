from proxycircuit import acquire, report

print(acquire("shop"))
report("shop", "10.0.0.1:8080", False)
try:
    acquire("news")
except RuntimeError as exc:
    print("全部出口停用", exc)

# TODO: 第二段演示（实现后补）
