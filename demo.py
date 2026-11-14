import importlib

from tablearchive import query

module = importlib.import_module("tablearchive.archive")
module._rows.extend([{"city": "sh", "v": 1}])
print(query("t", {"city": "sh"}))

# TODO: 第二段演示（实现后补）
