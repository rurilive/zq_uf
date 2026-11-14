from urlcanon import merge, same

merge("http://a/x?utm=1", "http://a/x")
merge("http://a/x", "http://a/x/")
print("a and c same:", same("http://a/x?utm=1", "http://a/x/"))

# TODO: 第二段演示（实现后补）
