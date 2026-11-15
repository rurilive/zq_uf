from profiler2 import observe, profile

observe([{"v": 1.0}, {"v": 2.0}, {"v": 1000.0}])
print(profile("v"))

# TODO: 第二段演示（实现后补）
