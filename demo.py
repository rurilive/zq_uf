from crawlmerge2 import merge, report

first = {"batch": "1", "rows": [{"key": "a", "value": 1}, {"key": "b", "value": 2}]}
second = {"batch": "2", "rows": [{"key": "a", "value": 9}]}
print(merge([first, second]))
print(report())

# TODO: 第二段演示（实现后补）
