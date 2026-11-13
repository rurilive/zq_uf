from watermark import Window

window = Window(size=5)
window.push({"ts": 1})
window.push({"ts": 9})
print(window.advance(10))

# TODO: 第二段演示（实现后补）
