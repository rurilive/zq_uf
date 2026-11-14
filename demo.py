from uafilter import headers, report, stats

print(headers("shop"))
print(report("shop", 403))

# 第二段：模拟 shop 当前 UA 被封——连续 403 且响应带封禁特征，自动切换备用 UA 并冷却
report("shop", 403)
report("shop", 403, response="Access Denied: captcha required")
print(headers("shop"))   # 已切换到备用 UA
print(stats("shop"))     # 当前头、切换记录（含原因）、封禁统计与冷却剩余时间
