"""两次改版演示：广告+布局改版只记审计，价格改版触发入库。"""

from changedetect import diff, should_ingest

BASE = """<html><body>
<div id="product">
  <h1 class="title">机械键盘</h1>
  <p class="price">¥199</p>
  <p class="stock">现货</p>
</div>
<div class="footer">客服 10000</div>
<div class="ad-slot">广告A</div>
<script>track(1)</script>
</body></html>"""

# 改版一：换广告、改脚本、把库存挪到价格前面（纯布局调整）
REV_LAYOUT = """<html><body>
<div id="product">
  <h1 class="title">机械键盘</h1>
  <p class="stock">现货</p>
  <p class="price">¥199</p>
</div>
<div class="footer">客服 10000</div>
<div class="ad-slot">广告B</div>
<script>track(2)</script>
</body></html>"""

# 改版二：在改版一的基础上调价
REV_PRICE = REV_LAYOUT.replace("¥199", "¥179")


def show(title, d):
    print(title)
    print("  变更节点:", d["changed_nodes"],
          "移动:", d["moves"], "插入:", d["inserts"], "删除:", d["deletes"])
    print("  字段差异:", d["fields"])
    print("  分级:", d["grade"], "入库:", should_ingest(d))
    print("  审计:", d["audit"])


show("改版一（广告+布局）", diff(BASE, REV_LAYOUT))
show("改版二（价格调整）", diff(REV_LAYOUT, REV_PRICE))
