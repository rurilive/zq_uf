from linkguard import follow

print(follow("http://s/a", lambda url: {"status": 200, "from": url}))

# TODO: 第二段演示（实现后补）
