from hostshield import acquire, release

for site in ("shop", "news", "shop"):
    print(acquire(site))
print(release("shop", 0.8, 200))

# TODO: 第二段演示（实现后补）
