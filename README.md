# siteleasectl

按站点的并发租约：全局上限 LIMIT=3 不变，每站可用 set_site_limit 配独立上限（未配置则只受全局上限约束）。

## 租期与回收

- 租约默认租期 DEFAULT_TTL=30s，lease(site, priority, ttl=秒) 可覆盖。
- 到期由 reclaim_expired() 或任意操作的惰性扫描自动回收，进程异常退出后额度随到期释放，不泄漏。
- release/reclaim_expired 幂等：重复调用返回 False/空列表，不重复扣额度。
- 租约 id 全局唯一且不复用，同一时刻只属于一个持有者；is_active 可查有效性。

## 抢占与检查点

- priority 越大越优先（默认 5）；容量满时仅允许抢占严格更低优先级的租约。
- 站点满只在本站内选牺牲品，全局满在全部租约中选优先级最低（并列取最早授予）者。
- 被抢占租约立即失效；其 checkpoint() 保存的检查点随抢占记录保留，任务据此恢复。
- 无法抢占时请求记入等待队列并抛 LeaseLimitError；参数非法抛 ValueError；给失效租约落检查点抛 LeaseStateError。

## 输出

- usage()：全局用量与各站占用；status()：各站占用、等待队列、抢占记录。

## 运行

    python3 demo.py
    python3 -m unittest discover -s tests
