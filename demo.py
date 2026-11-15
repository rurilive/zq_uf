from partgc2 import gc, pin

print(pin("p2"))
print(gc({"existing": ["p1", "p2", "p3"], "keep": ["p2"]}))

# TODO: 第二段演示（实现后补）
