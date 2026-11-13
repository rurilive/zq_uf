from pipelineview import build, impact

jobs = [{"name": "extract"}, {"name": "clean", "depends": ["extract"]}]
print(build(jobs))
print(impact("extract"))

# TODO: 第二段演示（实现后补）