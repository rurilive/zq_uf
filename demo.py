from rateadapt import acquire, feedback

print(acquire("shop"))
print(feedback("shop", 429, 3.2))

# TODO: 第二段演示（实现后补）
