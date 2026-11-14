# siteleasectl

按站点的并发租约：每站独立上限（SITE_LIMIT=2），全局总额度（GLOBAL_LIMIT=3），
租约带租期和优先级，高优可抢占低优，过期自动回收。

## 租期口径
- 每条租约租期 LEASE_TTL 秒（默认 30），到期自动回收，无需主动释放。
- 回收是惰性的：任意 lease/release/usage 调用都会先清理过期租约。
- release 幂等：重复释放返回 False，未知 id 抛 ValueError。
- 进程退出时（atexit）全部租约标记回收，额度不泄漏。

## 抢占口径
- 站点满（或全局满）时，优先级严格更高的请求抢占优先级最低的租约。
- 被抢占租约立即失效，检查点经 register_checkpoint 回调落下，checkpoint_of 可读。
- 无法抢占的请求进入等待队列，额度释放后按优先级补位。
- usage() 输出各站占用、等待队列、补位记录和抢占记录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
