"""演示：两组“同内容、不同写法”的变体页面如何得到同一指纹与等价类。

运行：python3 demo.py
"""

from htmlcanon import analyze, same

# 第一组：新闻文章页。变体 B 改大小写、属性顺序、空白、注释、脚本和自闭合；
# 两侧广告都放在隐藏容器里（隐藏内容不参与规范化，广告差异不影响正文指纹）。
NEWS_A = """
<!DOCTYPE html>
<html>
  <head><title>News</title></head>
  <body>
    <nav class="menu"><a href="/">首页</a><a href="/about">关于</a></nav>
    <article id="post-1" class="post featured">
      <h1>城市更新计划发布</h1>
      <p>市政府今天公布了新一轮 城市更新计划，覆盖十二个片区。</p>
    </article>
    <div aria-hidden="true" class="ad-slot">
      <img src="ad.png" alt="广告">
    </div>
  </body>
</html>
"""

NEWS_B = """
<!-- 镜像页面，模板完全不同 -->
<HTML>
<HEAD><TITLE>News</TITLE><script>track()</script></HEAD>
<BODY>
  <NAV class='menu'><a href='/'>首页</a><a href='/about'>关于</a></NAV>
  <ARTICLE class='featured post' id='post-1'>
    <H1>城市更新计划发布</H1>
    <P>市政府今天公布了新一轮   城市更新计划，覆盖十二个片区。</P>
  </ARTICLE>
  <div class='ad-slot' aria-hidden='true'>
    <img alt='广告' src='ad.png'/>
  </div>
</BODY>
</HTML>
"""

# 第二组：商品/表单页。变体 B 换布尔属性写法、自闭合、隐藏域和属性顺序。
FORM_A = """
<form action="/buy" method="post" id="order">
  <input type="hidden" name="csrf" value="tok">
  <label for="qty">数量</label>
  <input type="number" id="qty" name="qty" value="1" required="required">
  <button type="submit" disabled="disabled">提交</button>
</form>
"""

FORM_B = """
<FORM id='order' method='post' action='/buy'>
  <input name='csrf' type='hidden' value='tok' />
  <label for='qty'>数量</label>
  <input required name='qty' id='qty' type='number' value='1' />
  <button disabled type='submit'>提交</button>
</FORM>
"""


def show(title, html):
    result = analyze(html)
    print(f"--- {title} ---")
    print("规范化文本:", result.canonical)
    print("规则命中  :", result.rule_counts)
    print("块指纹    :")
    for block in result.blocks:
        start, end = result.source_of(block.index)
        print(
            f"  #{block.index} <{block.tag}> {block.fingerprint[:16]} "
            f"原文[{start}:{end}] {block.text[:36]!r}"
        )
    print("等价类    :", result.equivalence_class)
    print()
    return result


def main():
    print("================ 第一组：新闻文章 ================")
    ra = show("变体 A（标准写法）", NEWS_A)
    rb = show("变体 B（大小写/顺序/噪声差异）", NEWS_B)
    print("same(A, B) =", same(NEWS_A, NEWS_B))
    print("块指纹逐块相同:",
          [b.fingerprint for b in ra.blocks] == [b.fingerprint for b in rb.blocks])

    print("\n================ 第二组：商品表单 ================")
    rc = show("变体 A（属性带值）", FORM_A)
    rd = show("变体 B（布尔/自闭合/隐藏域）", FORM_B)
    print("same(A, B) =", same(FORM_A, FORM_B))
    print("等价类一致:", rc.equivalence_class == rd.equivalence_class)

    print("\n================ 按块回查原文示例 ================")
    article = next(b for b in ra.blocks if b.tag == "article")
    start, end = ra.source_of(article.index)
    print(f"article 块原文区间 [{start}:{end}]:")
    print(NEWS_A[start:end])


if __name__ == "__main__":
    main()
