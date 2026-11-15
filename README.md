# tablelease

数据表写租约：保证同一张表同一时刻只有一个写者，租约带到期时间，
持有者崩溃后可被接管；重入安全、释放幂等、等待按到达顺序排队。

## 背景

最初实现只是进程内一个 `set`，存在三个问题：

1. 多进程/多任务同时写同一张表，后写覆盖先写；
2. 写锁没有期限，进程崩溃后表一直被锁；
3. 同一任务重复进入写流程，自己把自己锁死。

## 方案

用 SQLite（WAL 模式）持久化租约，跨进程可见、崩溃不丢。三张表：

- `leases`：持有者、到期时间、重入计数、fencing token；
- `waiters`：FIFO 等待队列（自增 id 决定到达顺序）；
- `lease_audit`：发放、重入、接管、拒绝、释放等全部审计事件。

关键语义：

- **独占 + FIFO**：同表同时只有一个写者；服务端强制队头优先，
  非队头等待者即使遇到租约空闲也不能插队；
- **到期接管**：租约记录持有者和到期时间，过期才允许接管，
  接管写审计，并返回 `taken_over` / `previous_owner`；
- **重入安全**：同一持有者重复获取只增加引用计数，绝不自锁，
  释放时计数减一、归零才真正释放；
- **续约核对持有者**：`renew` 校验 owner 且租约未过期，否则抛 `LeaseError`；
- **释放幂等**：重复释放、非持有者释放、释放不存在的租约都不报错，
  用 `released` / `duplicate` 区分；等待者放弃排队会自动离队；
- **fencing token 防覆盖**：每次新发放（含接管）生成更大的 token，
  `FencedWriter` 拒绝小 token 的迟到写入，接管后旧进程的数据无法覆盖新数据。

返回统一包含：持有者 `holder`、剩余时间 `expires_in`、等待位置
`wait_position`、是否接管 `taken_over`。

## 用法

```python
from tablelease import configure, acquire, release, renew, status

configure(db_path="/var/lib/tablelease/leases.db")

resp = acquire("orders", "etl-1", ttl=30)      # 获取（可 block=True 排队）
renew("orders", "etl-1", 30)                   # 续约，核对持有者
release("orders", "etl-1")                    # 幂等释放，可重复调用
status("orders")                               # 只读，跨进程查看
```

## 进程崩溃演练

子进程拿租约写数据后 `os._exit` 硬退出（不释放），父进程等其过期后接管，
并验证旧持有者的迟到写入被 fencing 拒绝、数据未被覆盖：

```bash
python3 scripts/crash_drill.py drill /tmp/drill.db 1
```

## 测试

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
