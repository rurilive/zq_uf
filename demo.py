from lineage import Lineage

lineage = Lineage()
lineage.record({"name": "job1", "inputs": ["raw"], "outputs": ["clean"]})
print(lineage.impact("raw"))

# TODO: 第二段演示（实现后补）
