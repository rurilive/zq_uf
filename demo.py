from idemwrite import upsert

store = upsert([{"id": 1, "v": "a"}], ["id"])
upsert([{"id": 1, "v": "b"}], ["id"], store)
print(store)

# TODO: 第二段演示（实现后补）
