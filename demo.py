from siteleasectl import lease, release, usage

for site in ("shop", "shop", "news"):
    lease(site)
print(usage())
release("lease-1")
print(usage())

# TODO: 第二段演示（实现后补）
