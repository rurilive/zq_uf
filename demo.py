from listwatch import diff

OLD = [{"title": "文章一", "order": 1}, {"title": "文章二", "order": 2}]
NEW = [{"title": "文章三", "order": 1}, {"title": "文章二", "order": 2}]

result = diff(OLD, NEW)
print("新增", [item["title"] for item in result["added"]])
print("删除", [item["title"] for item in result["removed"]])

# TODO: 第二段演示（实现后补）
