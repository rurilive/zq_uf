from apipage import walk

PAGES = {1: {"items": ["a"], "next_page": 2}, 2: {"items": [], "next_page": None}}
print(walk(lambda p: PAGES[p]))

# TODO: 第二段演示（实现后补）
