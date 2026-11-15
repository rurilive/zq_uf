from crawlrecover import mark, recover

mark("task-a", "page-1")
mark("task-a", "page-2")
print(recover("task-a"))

# TODO: 第二段演示（实现后补）
