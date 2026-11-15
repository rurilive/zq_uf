from tableversion import commit, rollback

print(commit({"name": "orders", "rows": [1, 2, 3]}))
print(commit({"name": "orders", "rows": [4, 5]}))
print(rollback(1))

# TODO: 第二段演示（实现后补）
