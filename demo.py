from privpath import report, walk

OPS = [
    {"op": "read-config", "needs": "user", "effect": "admin"},
    {"op": "dump-db", "needs": "admin", "effect": "root"},
]

print(walk({"account": "dev", "role": "user", "ops": OPS}))
print(walk({"account": "ops", "role": "admin", "ops": OPS}))
print(report())

# TODO: 第二段演示（实现后补）
