from irplaybook import open, timeline

print(open({"id": "e1", "type": "ransomware", "operator": "li", "done": ["isolate"]}))
print(timeline("e1")["steps"])
print(timeline("e1")["closable"])

# TODO: 第二段演示（实现后补）
