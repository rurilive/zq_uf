from linkfollow import discover

PAGE = '<a href="http://site/a">A</a><a href="/b">B</a><a href="http://other/x">X</a>'
print(discover(PAGE, depth=2))

# TODO: 第二段演示（实现后补）
