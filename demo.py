"""演示规则收紧时的差异分析、定向失效、审计与回滚。"""

from robotsdiff import affected, allowed, audit, cache, diff, rollback, update

SOURCE = "https://shop.example/robots.txt"
FETCHED = "2026-11-15T08:00:00+00:00"

# 初始规则：仅 /private 禁止
update("shop", {"disallow": ["/private"]}, source=SOURCE, fetched_at=FETCHED)
print("初始判定:")
print("  /private/1 ->", allowed("shop", "/private/1"))  # False
print("  /promo/1   ->", allowed("shop", "/promo/1"))    # True
print("  /public/1  ->", allowed("shop", "/public/1"))   # True

# 规则收紧：新增禁止 /promo，旧规则下 /promo/1 的放行缓存必须失效
print("\n规则收紧（新增 disallow /promo）:")
update(
    "shop",
    {"disallow": ["/private", "/promo"]},
    source=SOURCE,
    fetched_at="2026-11-15T10:30:00+00:00",
)
change = diff("shop")
print("  新增禁止:", change["newly_disallowed"])   # ['/promo']
print("  解除禁止:", change["lifted_disallowed"])  # []
print("  定向失效路径:", affected("shop"))         # ['/promo/1']
print("  保留的缓存:", cache("shop"))              # /private/1、/public/1 保留
print("  收紧后 /promo/1 ->", allowed("shop", "/promo/1"))  # False 重新判定

print("\n最近一条审计:")
entry = audit("shop")[-1]
for key in ("time", "source", "fetched_at", "summary", "invalidation"):
    print("  {0}: {1}".format(key, entry[key]))

print("\n回滚到上一版规则:")
rollback("shop")
print("  /promo/1 ->", allowed("shop", "/promo/1"))  # True
print("  审计动作序列:", [r["action"] for r in audit("shop")])
