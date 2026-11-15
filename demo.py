from pagecore import blocks, extract

html = "<nav>首页 推荐</nav><article><p>正文一段</p></article>"
print(extract(html))
print(blocks(html))

# TODO: 第二段演示（实现后补）
