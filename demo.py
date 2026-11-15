"""pagefinger 改版演示：广告/脚本噪声、近重复、短页面、站点阈值、规则扩展。"""
import json

from pagefinger import (
    fingerprint,
    similar,
    configure,
    configure_site,
    register_ad_rule,
    reset_config,
)


def show(title, value):
    print("=" * 8, title, "=" * 8)
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    print()


# 同一篇文章的两个版本：B 换了广告位、加了埋点脚本，正文完全没动。
article_a = """
<html><body>
  <article>
    <h1>页面指纹升级说明</h1>
    <p>本次升级把整页哈希改为正文分块指纹，先剔除脚本样式和广告再取文本。</p>
    <p>每个稳定段落单独取哈希，相似度按块重合率计算，局部改版不再拉低整体判定。</p>
    <p>空白和标点会做归一化处理，短页面自动降低判定阈值并单独给出置信度。</p>
    <p>指纹带版本号，后续算法升级后可以按版本批量重算历史页面的指纹数据。</p>
  </article>
  <div class="ad-slot"><p>开屏广告招商热线一三八零零零零零零零零</p></div>
  <script>window.adRefresh && window.adRefresh();</script>
</body></html>
"""
article_b = """
<html><body>
  <article>
    <h1>页面指纹升级说明</h1>
    <p>本次升级把整页哈希改为正文分块指纹，先剔除脚本样式和广告再取文本。</p>
    <p>每个稳定段落单独取哈希，相似度按块重合率计算，局部改版不再拉低整体判定。</p>
    <p>空白和标点会做归一化处理，短页面自动降低判定阈值并单独给出置信度。</p>
    <p>指纹带版本号，后续算法升级后可以按版本批量重算历史页面的指纹数据。</p>
  </article>
  <div id="ads-banner" class="ggad"><p>换成了另一家广告联盟的投放内容文案</p></div>
  <script>tracker.log('impression', 9981);</script>
  <style>.ad-slot{position:fixed}</style>
</body></html>
"""

fp = fingerprint(article_a)
show("单页指纹（版本号/块数/块样本/短页面标记/置信度）", {
    "version": fp["version"],
    "hash": fp["hash"],
    "blocks": fp["blocks"],
    "short": fp["short"],
    "confidence": fp["confidence"],
    "block_samples": fp["block_samples"],
    "stats": fp["stats"],
})

show("改版页面（广告/脚本/样式全换，正文不动）", similar(article_a, article_b))

# 近重复：B 在同一篇文章上新增一段，旧块全部保留。
article_c = article_b + "<div><p>新增的更新日志段落，同样需要达到稳定块的最短长度要求。</p></div>"
show("近重复页面（新增一个段落）", similar(article_a, article_c))

# 短页面：只有两个短块，自动降阈，置信度单列。
short_a = "<p>这是一个很短的落地页正文块。</p>"
short_b = "<p>这是一个很短的落地页正文块。</p><p>第二段也是短块，内容与前者完全不同。</p>"
show("短页面（降阈 + 低置信度单独标注）", similar(short_a, short_b))

# 站点阈值：严格站点抬高阈值后，同一对近重复页面不再判相似。
configure_site("strict.example.com", threshold=0.95)
show("站点阈值 strict.example.com=0.95",
     similar(article_a, article_c, site="strict.example.com"))
reset_config()

# 规则扩展：站点自定义营销位 class，注册规则前营销位会被当成正文参与比对，
# 注册后才作为广告剔除。
page = "<p>这是一段足够长的正文，用来保证页面至少存在一个稳定块。</p>"
page_with_promo = page + "<div class='campaign-dock'><p>站点自营营销位文案也超过八个字。</p></div>"
before = similar(page, page_with_promo)
print("=" * 8, "扩展规则注册前（营销位被当成正文，重合率 0.5）", "=" * 8)
print("blocks b =", before["block_counts"]["b"], " score =", before["score"])
print()


def campaign_rule(tag, attrs):
    info = {k.lower(): (v or "") for k, v in attrs}
    return "campaign-dock" in info.get("class", "").split()


register_ad_rule(campaign_rule)
show("扩展规则注册后（营销位被识别为广告剔除，重合率 1.0）", similar(page, page_with_promo))
reset_config()

# 显式阈值入参与确定性：同页多次指纹一致。
show("显式 threshold=1.0（只接受完全重合）", similar(article_a, article_c, threshold=1.0))
f1, f2 = fingerprint(article_a), fingerprint(article_a)
print("同页重复计算指纹一致:", f1 == f2)
