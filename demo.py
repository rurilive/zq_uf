from robotscache import RobotsCache

ROUTES = {
    ("https", "example.com", 443): "User-agent: *\nDisallow: /private\n",
    ("https", "shop.example.com", 443): "User-agent: *\nDisallow:\n",
}

cache = RobotsCache(fetcher=lambda url: ROUTES.get(
    (url.split("://")[0], url.split("://")[1].split("/")[0].rsplit(":", 1)[0],
     int(url.split("://")[1].split("/")[0].rsplit(":", 1)[1]))))

# 第一段：缓存命中 + 子域继承/独立
print(cache.allowed("https://example.com/private/x"))   # 拒绝，reason 给出原因
print(cache.allowed("https://shop.example.com/private"))  # 子域用自己的规则，放行
print(cache.allowed("https://blog.example.com/private"))  # 子域无规则，继承主域，拒绝

# 第二段：已入队链接按旧规则完成，更新只影响新请求
ticket = cache.enqueue("https://example.com/new-page")
ROUTES[("https", "example.com", 443)] = "User-agent: *\nDisallow: /\n"
print(cache.validate_update("example.com", ["https://example.com/new-page"]))  # 拒绝范围扩大，不启用
print(cache.allowed_queued(ticket))  # 仍按旧规则放行
print(cache.alerts())  # 校验失败告警，旧规则保留
