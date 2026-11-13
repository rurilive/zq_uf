from loginflow import run

STEPS = [lambda ctx: {"token": "abc"}, lambda ctx: {"logged": bool(ctx["token"])}]
print(run("acme", STEPS))

# TODO: 第二段演示（实现后补）
