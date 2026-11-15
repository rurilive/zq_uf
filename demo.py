from dagprop import rerun, run

print(run({"nodes": ["extract", "transform", "load"]}))
print(rerun(["transform"]))

# TODO: 第二段演示（实现后补）
