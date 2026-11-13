from aggretract import Aggregator

agg = Aggregator()
agg.apply([{"key": "a", "value": 2}, {"key": "a", "value": 3}])
print(agg.query("a"))

# TODO: 第二段演示（实现后补）
