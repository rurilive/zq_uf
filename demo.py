from contenthash import DEFAULT_THRESHOLD, fingerprint, similar

# 同一篇文章的两个版本：导航、广告、脚本和样式全部改版，正文保留。
old_page = """
<html><head><style>.nav{color:red}</style>
<script>ga('send','pageview');</script></head><body>
<nav>home news about</nav>
<div class="ad-banner">old banner: big sale</div>
<article>
  <h1>Stable Title</h1>
  <p>First paragraph keeps the same words.</p>
  <p>Second paragraph also stays identical.</p>
  <p>Third paragraph remains here as well.</p>
</article>
<footer>old copyright 2025</footer>
</body></html>
"""

new_page = """
<html><head><style>.menu{color:blue}</style>
<script>tracker.track('page');</script></head><body>
<nav>home news about login signup</nav>
<div id="googleads">new sponsored slot here</div>
<article>
  <h1>Stable Title</h1>
  <p>First paragraph keeps the same words.</p>
  <p>Second paragraph also stays identical.</p>
  <p>Third paragraph remains here as well.</p>
</article>
<footer>new copyright 2026</footer>
</body></html>
"""

# 另一篇只重合部分正文的页面，用于对比不同内容的判定。
other_page = """
<nav>home news</nav><article>
  <h1>Stable Title</h1>
  <p>First paragraph keeps the same words.</p>
  <p>A completely different second paragraph appears.</p>
  <p>And the third paragraph is rewritten too.</p>
</article>
"""


def show(label, html):
    fp = fingerprint(html)
    print(f"[{label}] 指纹={fp.hash[:16]}... 块数={fp.block_count} "
          f"短正文={fp.short} 置信度={fp.confidence}")
    for block in fp.blocks:
        print(f"    - {block}")
    return fp


fp_old = show("旧版页面", old_page)
fp_new = show("改版页面", new_page)
fp_other = show("其他文章", other_page)

print()
print("稳定指纹一致（改版不判新内容）:", fp_old.hash == fp_new.hash)
print()

for label, page in (("改版页面", new_page), ("其他文章", other_page)):
    result = similar(old_page, page, threshold=DEFAULT_THRESHOLD)
    print(f"旧版 vs {label}: 相似度={result.score} 重合块={result.matched} "
          f"阈值={result.threshold} 近重复={bool(result)}")
    print("  依据:", result.reason)

# 自定义阈值演示（非短正文才生效）：6 个块、4 块重合，Jaccard=0.5。
long_a = "".join(f"<p>long article paragraph {i}</p>" for i in range(6))
long_b = "".join(
    [f"<p>long article paragraph {i}</p>" for i in range(4)]
    + ["<p>brand new paragraph four</p>", "<p>brand new paragraph five</p>"]
)
print()
print("长正文 @ 默认阈值 0.8:", bool(similar(long_a, long_b)))
print("长正文 @ 放宽阈值 0.5:", bool(similar(long_a, long_b, threshold=0.5)))
