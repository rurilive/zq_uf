"""linkrewrite 演示：shop / news 两组站点。

运行：python3 demo.py
"""

import json

from linkrewrite import InvalidRule, get_registry, history, rewrite, rules


def show(title, payload):
    print("=" * 72)
    print(title)
    print("-" * 72)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    print()


registry = get_registry()

# ============================================================ 站点一：shop
show(
    "站点 shop 的规则与静态冲突（channel 被两条规则竞争，按优先级仲裁）",
    rules("shop", registry=registry),
)

url_shop = "https://shop.example.com/list?utm_source=feed&camp=618&channel=feed&page=3&spm=a1z"
first = rewrite(url_shop, "shop", registry=registry)
show("shop 首次重写：追踪参数删除、分页保留、page_size 注入、冲突解释", first)

second = rewrite(first["after"], "shop", registry=registry)
show(
    "shop 幂等校验：对重写结果再次重写，after 必须与上次一致（%s）"
    % ("通过" if second["after"] == first["after"] else "失败"),
    {"before": second["before"], "after": second["after"]},
)

# ============================================================ 站点二：news
show(
    "站点 news 的规则与静态冲突（同优先级 utm_source 竞争，按注册顺序仲裁）",
    rules("news", registry=registry),
)

url_news = (
    "https://news.example.com/article/123?utm_source=mail&utm_campaign=daily"
    "&pg=2&spm=x&ref=home"
)
show(
    "news 文章页重写：路径命中白名单规则，未在白名单的普通参数被清理",
    rewrite(url_news, "news", registry=registry),
)

show(
    "news 栏目页（不同路径命中另一条规则，sort 保留）",
    rewrite(
        "https://news.example.com/section/tech?pg=4&sort=hot&utm_source=x",
        "news",
        registry=registry,
    ),
)

# ============================================================ 未命中：原样返回并记录
before_miss = rewrite(
    "https://a.example.com/p?page=2", "a", registry=registry
)
show("未配置站点 a：原样返回，hit 为空且写入记录", before_miss)

# ============================================================ 注入合法性校验
try:
    from linkrewrite import Rule

    registry.add_rule(
        "shop",
        Rule(
            id="bad-inject",
            priority=1,
            pagination={"inject": {"page": {"value": "999", "max": 100}}},
        ),
    )
except InvalidRule as exc:
    show("非法注入在注册时被拒绝（不会产生半成品规则）", {"error": str(exc)})

# ============================================================ 规则变更回滚
rev_before = registry.current_revision("shop")
registry.add_rule(
    "shop",
    Rule(
        id="shop-temp-experiment",
        priority=200,
        description="临时实验规则",
        other={"delete": ["debug"]},
    ),
)
rev_after = registry.current_revision("shop")
rolled_to = registry.rollback("shop", rev_before)
show(
    "规则回滚：加一条临时规则（rev %s -> %s）后回滚到 rev %s，生成新版本 %s"
    % (rev_before, rev_after, rev_before, rolled_to),
    {
        "revisions": registry.revisions("shop"),
        "current_rules": [item["id"] for item in rules("shop", registry=registry)["rules"]],
    },
)

# ============================================================ 重写记录
show("最近 3 条重写记录（含未命中原样返回的那次）", history(limit=3, registry=registry))
