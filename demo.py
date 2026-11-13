from proxysticky import acquire

for step in range(3):
    print("step", step, acquire("shop", "s1"))

# TODO: 第二段演示（实现后补）
