from pagecore import Extractor, blocks, diff_results, extract

ARTICLE = """
<nav><a>首页</a><a>推荐</a></nav>
<article>
<p>正文一段，内容密度高、链接少，应当被当作正文保留下来。</p>
<p>正文二段，同样保持足够的字数，与第一段一起构成文章主体。</p>
</article>
<aside class="recommend"><a>推荐文章一</a><a>推荐文章二</a></aside>
"""

result = extract(ARTICLE)
print("正文:", result["text"])
print("把握程度:", result["confidence"], "保留块:", result["kept"], "噪声块:", result["noise"])
print("块明细:", [(b["tag"], b["reason"], b["score"]) for b in blocks(ARTICLE)])

short = extract("<article><p>短文一段。</p><p>短文二段。</p></article>")
print("短文保留:", short["text"], "把握程度:", short["confidence"])

extractor = Extractor()
first = extractor.extract(ARTICLE)
stale = extractor.extract("")  # 改版后规则失效抽空了，保留上一次结果
print("失败兜底:", stale["stale"], stale["text"] == first["text"])

new = extract(ARTICLE.replace("正文二段", "改版后的正文二段"))
print("新旧对比:", diff_results(first, new))
