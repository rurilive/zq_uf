from dagcrawl import run

ctx = run({"steps": [lambda c: {**c, "list": 3}, lambda c: {**c, "detail": c["list"] * 2}]}, {})
print(ctx)

# TODO: 第二段演示（实现后补）
