from dupfinder import DupFinder

finder = DupFinder()
finder.index([{"id": "a", "text": "hello"}, {"id": "b", "text": "hello"}])
print(finder.query({"text": "hello"}))

# TODO: 第二段演示（实现后补）
