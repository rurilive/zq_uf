from readclean import extract

SAMPLE = """<html><head><title>示例文章</title></head>
<body><nav>首页 关于</nav>
<p>第一段内容。</p>
<p>第二段内容。</p>
<footer>版权</footer></body></html>
"""

result = extract(SAMPLE)
print("标题", result["title"])
for part in result["paragraphs"]:
    print("段落", part)

# TODO: 第二段演示（实现后补）
