# sessionaudit

纯标准库的采集会话管理：使用写审计、过期自动失效并安全回收，接口保持 `use(account)` / `sweep(now)`。

## 用法

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

```python
from sessionaudit import use, sweep, query_audit, export_state

use("acc-1")                 # 未过期复用同一会话，每次写审计
sweep()                      # 过期失效 + 安全回收 + 审计裁剪
query_audit(account="acc-1") # 按账号查审计
query_audit(site="site-a")   # 按站点查审计
export_state("state.json")   # 导出会话状态
```

需要可测试/多站点/自定义保留期时用 `SessionManager`：`store_dir`（会话与审计目录）、`site`、`ttl`、`audit_retention`（`None` 永久保留）、`clock`（统一时钟）、`retry_attempts`、`remove`/`sleep`（注入删除器与等待）。

## 审计口径

- 每次 `use` 写一条 `use` 记录，字段：`id`、`time`、`site`、`account`、`session`、`event`、`detail`；另有 `expire`（过期失效）、`reclaim`（回收成功）、`skip_reclaim`（在跑跳过）、`reclaim_failed`（回收失败）、`audit_pruned`（日志裁剪）事件。
- 审计落盘为目录下的 JSON Lines `audit.log`，同目录多站点管理器共享一份日志；`query_audit(account=, site=, event=)` 可组合查询，参数为 `None` 不过滤。
- `use()` 返回值里的 `audit` 保持为空列表（历史明细只走日志查询），兼容旧调用方。
- 保留期由 `audit_retention` 控制，`sweep` 时裁剪超时记录；设为 `None` 永久保留。

## 回收口径

- 统一时钟：`sweep(now)` 显式传 `now`，未传则取注入时钟；滑动 TTL 按会话最后使用时间判定（`last_used_at + ttl <= now` 即过期）。
- 过期先置为 `expired` 并脱离复用（再 `use` 会新建会话），再回收磁盘文件。
- 安全回收：会话有在跑任务（`task()` 上下文，`busy > 0`）时只失效不删除，记入 `skipped`；任务结束后的下一轮 `sweep` 再回收。
- 回收前先 `close` 释放文件句柄，再删除文件；删除失败按 `retry_attempts` 重试，仍失败记入 `failed`，下一轮自动重试。
- `sweep` 返回 `now`、`expired`、`reclaimed`、`skipped`、`failed`、`audit`（本轮事件）、`audit_pruned`。
- `export_state(path=None)` 导出全部会话的状态（状态、忙碌数、使用次数、到期时间、句柄是否打开等），给路径时同时写 JSON 文件。
