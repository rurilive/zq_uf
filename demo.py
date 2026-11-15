"""pagecore 用法演示：抽取、块定位、按站点配置、失败回退与差异对比。"""
import os
import tempfile

from pagecore import (PageStore, blocks, compare_text, extract, set_site_config)


def article_page(paragraphs, wrapper="article"):
    body = "".join("<p>%s</p>" % text for text in paragraphs)
    return (
        "<html><body>"
        "<nav><a href='/'>首页</a><a href='/hot'>热门</a></nav>"
        "<%s class='post'>%s</%s>"
        "<aside class='related'><h3>相关推荐</h3><a href='/a'>另一篇</a></aside>"
        "<section class='comments'><p>评论内容不算正文。</p></section>"
        "</body></html>"
    ) % (wrapper, body, wrapper)


print("=" * 60)
print("1) 列表页 + 导航 + 评论区，只留正文")
html = (
    "<nav>首页 推荐</nav>"
    "<article><h1>文章标题</h1>"
    "<p>第一段正文，讲清楚这篇到底在说什么，长度足够参与密度打分。</p>"
    "<p>第二段正文，说明噪声块如何单独标出、短正文为什么保留。</p></article>"
    "<section class='comments'><p>评论区的内容不应算正文。</p></section>"
)
result = extract(html)
print("正文：", result["text"])
print("把握程度：", result["confidence"], "| 页面类型：", result["page_type"])
print("保留块：", [(b["locator"], b["score"]) for b in result["kept"]])
print("噪声块：", [(b["locator"], b["reason"]) for b in result["noise"]])
print("剔除块：", [(b["locator"], b["reason"]) for b in result["dropped"]])

print()
print("2) 短文保留，把握程度自动降低")
short = "<article><p>只有两段。</p><p>短文也要保留。</p></article>"
short_result = extract(short)
print("正文：", short_result["text"], "| short:", short_result["short"],
      "| confidence:", short_result["confidence"])

print()
print("3) 站点改版：只改配置，不动算法")
changed = (
    "<div class='partnerbox'><p>合作推广位，这是改版后新增的容器。</p></div>"
    "<article><p>第一段正文，长度足够参与测试。</p>"
    "<p>第二段正文，长度也足够参与测试。</p></article>"
)
print("默认规则：", extract(changed, site="news.example.com")["text"])
set_site_config("news.example.com", noise_hints=["partnerbox"])
print("站点规则：", extract(changed, site="https://news.example.com/1")["text"])

print()
print("4) blocks() 返回所有块，直接定位哪些被当成正文")
for block in blocks(html):
    print("  #%d %-8s keep=%-5s score=%-6s %s"
          % (block["index"], block["tag"], block["keep"], block["score"],
             block["locator"]))

print()
print("5) 改版切换：先抽样对比，确认没丢段落再切换；失败保留上一次")
root = tempfile.mkdtemp(prefix="pagecore-demo-")
store = PageStore(root, retention_days=7)
key = "https://news.example.com/post/1"
v1 = article_page(["第%d段正文内容，长度足够，说明这篇到底在讲什么。" % i for i in range(1, 5)])
v2 = article_page(["第%d段正文内容，长度足够，说明这篇到底在讲什么。" % i for i in range(1, 5)]
                  + ["第5段：改版后新增的正文。"])
store.extract_safe(key, v1)
store.stage(key, v2)
print("差异：missing=%d added=%d retained=%.2f"
      % (len(store.diff(key)["missing"]), len(store.diff(key)["added"]),
         store.diff(key)["retained"]))
store.promote(key)

lossy = article_page(["只剩这一段正文，其他段落都被改版弄丢了。"])
store.stage(key, lossy)
print("丢段落时 diff：", store.diff(key)["missing"])
try:
    store.promote(key)
except Exception as exc:
    print("拒绝切换：", type(exc).__name__, exc)

failed = store.extract_safe(key, "<html><body><div class='empty'></div></body></html>")
print("抽取失败 stale=%s reason=%s 仍保留 %d 字"
      % (failed["stale"], failed["reason"], len(failed["result"]["text"])))

print("回退到上一版：", store.rollback(key)["source"])
print("保留期目录：", root)

print()
print("6) 两版正文直接对比")
diff = compare_text("第一段\n\n第二段\n\n第三段", "第一段\n\n第三段")
print("missing:", diff["missing"], "| retained:", diff["retained"])
