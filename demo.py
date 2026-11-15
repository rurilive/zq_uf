from pagecore import blocks, configure, diff, extract

page = (
    "<nav>首页 推荐 热榜</nav>"
    "<article>"
    "<p>正文第一段，内容足够长才会有较高的把握程度，短正文则保留但把握程度降低。</p>"
    "<p>正文第二段，导航、评论区和推荐位都不会算进正文。</p>"
    "</article>"
    '<div class="recommend"><a href="/a">推荐阅读一</a><a href="/b">推荐阅读二</a></div>'
)

r = extract(page)
print("正文:", r["text"])
print("把握:", r["confidence"])
print("剔除:", [(b["tag"] or "loose", b["reason"]) for b in r["noise"]])

# 短正文：保留但把握低
short = extract("<article><p>只有两句。</p></article>")
print("短正文:", short["text"], short["confidence"])

# 规则按站点覆盖：该站推荐位也算正文
configure("example.com", noise_patterns=())
print("站点覆盖:", extract('<div class="recommend">这段在该站算正文，字数也够。</p></div>',
                           site="example.com")["confidence"])

# 改版失败：保留上一次结果，人工 diff 对比
old = extract(page, url="demo")
changed = extract(page.replace("第二段", "第二段（更新）"), url="demo")
print("正文差异:", diff(old, changed))
failed = extract("<div></div>", url="demo")
print("失败回退:", failed["fallback"], failed["text"] == changed["text"])
