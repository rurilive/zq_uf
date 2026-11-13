"""demo：对三类典型页面运行抽取并打印结果。"""
from readclean import extract

ARTICLE = """<html><head><title>城市更新进入新阶段</title>
<meta property="article:published_time" content="2026-11-01T08:00:00+08:00"></head>
<body>
<nav><a href="/">首页</a><a href="/news">新闻</a><a href="/about">关于我们</a></nav>
<article>
<p>记者走访三地记录城市更新的最新进展，采访对象包括规划师与老街居民。</p>
<p>规划部门表示，下一阶段将公布更详细的改造方案，并向社会公开征求意见。</p>
</article>
<div class="related"><a href="/a">相关推荐：旧城区改造十年回顾</a></div>
<div id="comment"><p>网友：期待改造后的样子！</p></div>
<footer>版权所有 2026 某报社</footer>
</body></html>"""

SHORT_PAGE = """<html><head><title>快讯</title></head>
<body><p>快讯：今日气温回升。</p><p>出行请注意防晒。</p></body></html>"""

TABLE_PAGE = """<html><head><title>月度销售数据</title></head>
<body>
<p>以下是本月各地区销售数据汇总，包含销量与环比变化。</p>
<table>
<tr><th>地区</th><th>销量</th><th>环比</th></tr>
<tr><td>华东</td><td>1200</td><td>+12%</td></tr>
<tr><td>华南</td><td>860</td><td>-3%</td></tr>
</table>
<p>数据来源：公司内部统计。</p>
</body></html>"""


def show(name, html):
    result = extract(html)
    print("=" * 20, name, "=" * 20)
    print("标题:", result["title"])
    print("发布时间:", result["published"] or "（未识别）")
    print("置信度:", result["confidence"])
    for part in result["paragraphs"]:
        print("段落:", part)
    print()


if __name__ == "__main__":
    show("新闻文章（含导航/推荐/评论）", ARTICLE)
    show("短页面", SHORT_PAGE)
    show("表格页", TABLE_PAGE)
