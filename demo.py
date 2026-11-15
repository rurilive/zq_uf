from pagetrack import changes, save

print(save("https://shop/1", "<p>v1</p>"))
print(save("https://shop/1", "<p>v2</p>"))
print(changes(0))

# TODO: 第二段演示（实现后补）
