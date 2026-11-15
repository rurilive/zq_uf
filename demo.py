from crawlflow import resume, run

print(run({"name": "demo"}, {"seed": "https://shop/"}))
print(resume("token-1"))

# TODO: 第二段演示（实现后补）
