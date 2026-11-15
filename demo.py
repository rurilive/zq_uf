"""两组页面演示：第一组正常页，第二组软 404 页（空页/占位页/跳转页/模板批量）。"""

import json

from detectsoft import judge, report


def run(title, pages):
    print("== %s ==" % title)
    for page in pages:
        r = judge(page)
        print("%-28s %-8s marks=%s" % (page["url"], r["kind"], r["marks"]))
    print()


# 第一组：正常页面
run("正常页面", [
    {"url": "https://shop/item/1", "site": "shop", "status": 200,
     "template": "item", "text": "复古机械键盘，全键无冲，现货发售。" * 3,
     "headers": {"content-type": "text/html"}},
    {"url": "https://shop/item/2", "site": "shop", "status": 200,
     "template": "item", "text": "人体工学椅，三年质保，今日下单立减。" * 3,
     "headers": {"content-type": "text/html"}},
    {"url": "https://shop/about", "site": "shop", "status": 200,
     "template": "page", "text": "我们是一家专注外设的店铺。" * 3,
     "headers": {"content-type": "text/html"}},
])

# 第二组：软 404 页面（同模板连续命中，触发模板级升级）
run("软404页面", [
    {"url": "https://shop/item/9001", "site": "shop", "status": 200,
     "template": "item-gone", "text": "",
     "headers": {"content-type": "text/html"}},
    {"url": "https://shop/item/9002", "site": "shop", "status": 200,
     "template": "item-gone", "text": "暂无内容，请稍后再来",
     "headers": {"content-type": "text/html"}},
    {"url": "https://shop/item/9003", "site": "shop", "status": 200,
     "template": "item-gone", "text": "x" * 40,
     "redirect": "https://shop/",
     "headers": {"content-type": "text/html"}},
    {"url": "https://shop/item/9004", "site": "shop", "status": 404,
     "template": "item-gone", "text": "missing",
     "headers": {"content-type": "text/html"}},
])

print("== 站点总览 ==")
print(json.dumps(report("shop"), ensure_ascii=False))
print("== 按天下钻 ==")
print(json.dumps(report("shop", by="day"), ensure_ascii=False, indent=1))
print("== 按模板下钻（含命中样本） ==")
print(json.dumps(report("shop", by="template"), ensure_ascii=False, indent=1))
