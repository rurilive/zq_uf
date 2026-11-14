from fieldpolicy import resolve
from fieldpolicy import policy

policy._global["masked"] = True
print(resolve("orders", "phone"))

# TODO: 第二段演示（实现后补）
