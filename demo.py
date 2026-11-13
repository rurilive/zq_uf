from paginate import walk

PAGES = {1: ["文章一", "文章二"], 2: ["文章三"]}
print(walk(lambda page: PAGES.get(page, [])))

# TODO: 第二段演示（实现后补）
