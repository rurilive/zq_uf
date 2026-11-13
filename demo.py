from encodepage import decode

body = "中文页面".encode("gbk")
print(decode(body, {"content-type": "text/html; charset=gbk"}))
print(decode("默认编码".encode("utf-8"), {}))

# TODO: 第二段演示（实现后补）
