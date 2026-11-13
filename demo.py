from signparam import sign

print(sign({"appid": "demo", "ts": "1700000000", "q": "键盘"}))
print(sign({"q": "键盘", "ts": "1700000000", "appid": "demo"}))

# TODO: 第二段演示（实现后补）
