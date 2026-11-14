import importlib

from lineageimpact import impact

module = importlib.import_module("lineageimpact.impact")
module.record("raw", "dwd")
module.record("dwd", "dm")
print(impact("raw"))

# TODO: 第二段演示（实现后补）
