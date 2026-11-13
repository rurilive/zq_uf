from versionflow import current, switch

switch("orders", "v1")
print(current("orders"))
print(switch("orders", "v2"))

# TODO: 第二段演示（实现后补）