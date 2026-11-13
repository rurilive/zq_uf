from cdcmerge import Merger

merger = Merger()
merger.apply([{"op": "insert", "key": "a", "value": 1}])
merger.apply([{"op": "update", "key": "a", "value": 2}])
print(merger.snapshot())

# TODO: 第二段演示（实现后补）
