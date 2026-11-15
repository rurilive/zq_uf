from pagecore import blocks, extract

LIST_PAGE = (
    "<html><body>"
    "<nav><a>首页</a><a>频道</a><a>登录</a></nav>"
    "<ul>"
    "<li><a>新闻标题一：采集系统上线</a></li>"
    "<li><a>新闻标题二：分布式架构实践</a></li>"
    "<li><a>新闻标题三：数据节点扩容</a></li>"
    "</ul>"
    "<footer>版权所有</footer>"
    "</body></html>"
)

ARTICLE_PAGE = (
    "<html><body>"
    "<nav><a>首页</a><a>推荐</a><a>登录</a></nav>"
    "<article>"
    "<p>本报讯，记者昨日从发布会现场了解到，新一代采集系统已经正式上线，"
    "覆盖了全国三十一个省市自治区的数据节点，日均处理页面超过两亿个。</p>"
    "<p>项目负责人介绍说，系统采用了全新的分布式架构，"
    "在保证稳定性的同时，将单页处理耗时压缩到了毫秒级。</p>"
    "</article>"
    "<aside><a>热门推荐：某产品发布会</a><a>猜你喜欢：行业动态</a></aside>"
    "<div class=\"comments\"><a>网友甲</a>：说得好！<a>网友乙</a>：顶一下</div>"
    "</body></html>"
)

NARROW_PAGE = (
    "<div class=\"col\"><p>清晨的山谷里雾气很重，能见度不足五十米，"
    "巡山员还是照常出发了。</p></div>"
    "<div class=\"col\"><p>他说这条路线走了二十年，哪里有暗沟、"
    "哪里有陡坡，闭着眼都数得出来。</p></div>"
    "<div class=\"col\"><p>傍晚回到管护站，他把当天的记录誊进台账，"
    "字迹工整，一笔不落。</p></div>"
)


def show(title, result):
    print("=" * 20, title, "=" * 20)
    print("正文:", result["text"])
    print("噪声:", result["noise"])
    print("块分数:", result["scores"])
    print("置信度:", result["confidence"], "低置信:", result["low_confidence"])
    print("命中规则:", result["rule"], "正文边界:", result["boundaries"])
    print()


show("列表页（无正文，全量保留 + 低置信）", extract(LIST_PAGE))
show("文章页（导航/推荐/评论进噪声）", extract(ARTICLE_PAGE))
show("窄栏文章 · 默认规则（短正文低置信）", extract(NARROW_PAGE))
show("窄栏文章 · 站点覆盖 narrow.example",
     extract(NARROW_PAGE, site="narrow.example"))
show("窄栏文章 · 回退上一版规则 rules_version=0",
     extract(NARROW_PAGE, site="narrow.example", rules_version=0))

print("=" * 20, "文章页逐块打分", "=" * 20)
for b in blocks(ARTICLE_PAGE):
    print(f"[{b['index']}] <{b['tag']}> score={b['score']} "
          f"link={b['link_density']} punct={b['punct_ratio']} "
          f"content={b['is_content']} | {b['text'][:24]}")
