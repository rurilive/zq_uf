from crawlbudget import LIMIT, consume, remaining

consume("shop", 10)
print("shop 用掉 10，剩", remaining("shop"))
consume("news", 5)
print("news 用掉 5 后全局剩", remaining("news"), "上限", LIMIT)

# TODO: 第二段演示（实现后补）
