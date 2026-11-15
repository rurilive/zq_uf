from pagetrack import (changes, pull, reset, rollback, save, subscribe,
                       versions)

reset()

# 首次收录 + 三次改版
print(save("https://shop/1", "<p>v1</p>"))
print(save("https://shop/1", "<p>v2</p>"))
print(save("https://shop/1", "<p>v3</p>"))
print(save("https://shop/1", "<p>v4</p>"))

# 同一内容重复保存，不产生新版本
print(save("https://shop/1", "<p>v4</p>"))

# 变更日志（三次改版）和全部历史版本
print(changes(0))
print(versions("https://shop/1"))

# 订阅方按序号拉增量，断线后续拉
sub = subscribe(site="shop")["subscription"]
save("https://shop/1", "<p>v5</p>")
print(pull(sub))
print(pull(sub))  # 已拉到最新，空

# 回滚到 v1：生成新版本，历史记录保留
print(rollback("https://shop/1", 1))
print(changes(0, site="shop"))
