from pagecycle import walk

PAGES = {
    "p1": {"url": "p1", "next": "p2"},
    "p2": {"url": "p2", "next": "p1"},
}

print(walk(lambda u: PAGES[u], start="p1", limit=6))

# TODO: 第二段演示（实现后补）
