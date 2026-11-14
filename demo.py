from csrftoken import get, invalidate

print(get("login", 1))
print(get("login", 1))
invalidate("login")

# TODO: 第二段演示（实现后补）
