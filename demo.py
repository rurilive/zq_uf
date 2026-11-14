from jobdep import run

print(run({"steps": [{"name": "extract", "fn": lambda s: None},
                     {"name": "load", "fn": lambda s: None}]}))

# TODO: 第二段演示（实现后补）
