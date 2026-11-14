from dedupwindow import advance, reset, seen

reset()
print(seen("a", 1), seen("a", 2))
print("state", advance(10))

# TODO: 第二段演示（实现后补）
