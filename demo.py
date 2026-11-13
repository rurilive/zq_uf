from flowstep import run


def open_page(ctx):
    return {"page": "报销单"}


def fill(ctx):
    return {"filled": ctx["page"] + "已填写"}


print(run([open_page, fill], {"user": "张三"}))

# TODO: 第二段演示（实现后补）
