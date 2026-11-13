from langdetect import detect

for text in ("机械键盘 299 元", "商品の詳細", "clavier mecanique", "a"):
    print(detect(text))

# TODO: 第二段演示（实现后补）
