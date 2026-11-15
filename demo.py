from retryquota import release, request

for _ in range(5):
    print(request("task-a", "shop"))
print(release("task-a"))

# TODO: 第二段演示（实现后补）
