from latejoin import join

FACTS = [{"id": 1, "v": 10}, {"id": 2, "v": 20}]
DIMS = [{"id": 1, "name": "旧"}, {"id": 1, "name": "新"}]
print(join(FACTS, DIMS))

# TODO: 第二段演示（实现后补）
