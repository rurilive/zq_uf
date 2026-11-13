from dqrules import run

ROWS = [{"id": "1", "name": "甲"}, {"id": "2", "name": ""}]
print(run(ROWS, {"required": ["id", "name"]}))

# TODO: 第二段演示（实现后补）
