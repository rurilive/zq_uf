# retryquota

重试配额的全局协调模块，纯 Python 标准库。

问题：原先每个任务各自计数、请求一律获批，所有任务一起重试会把站点瞬间
打满；没有全局上限；站点恢复后所有任务同时回满。

现在：按 **站点 + 全局两级配额** 发放重试许可，超额排队；同一站点同时重试
数量有上限；释放幂等；租约超时自动归还；协调状态可跨进程查看。

```bash
python3 demo.py
python3 -m unittest discover -s tests
python3 -m retryquota /tmp/retryquota-demo-state.json   # 跨进程查看状态
```

## 接口

原有接口不变，模块级默认协调器：

```python
from retryquota import request, release

permit = request("task-a", "shop")   # {"granted": bool, "permit_id", "round", ...}
release("task-a")                    # 释放该任务最早持有的一张许可
```

`request(task, site)` 返回：

| 字段 | 含义 |
| --- | --- |
| `granted` | 是否当场拿到许可 |
| `queued` / `position` | 是否排队、队内位置（1 起） |
| `blocked_by` | 排队原因：`site` / `global` / `site+global` |
| `permit_id` / `expires_at` | 许可凭证与租约到期时间 |
| `round` | 该任务当前持有的许可数 |
| `expired_permits` / `granted_from_queue` | 本次协调顺带归还的超时数、补发的排队任务 |

`release(task)` 返回 `released`（本次是否真的释放）、`duplicate`（重复释放）、
`expired`/`stale`（释放的是已超时许可）、`reason`，以及本次顺带补发的
`granted_from_queue`。

扩展接口：

```python
from retryquota import QuotaCoordinator, configure, fail, set_priority, sweep_expired, snapshot

configure(global_limit=64, site_limit=16, lease_seconds=300, state_path="/tmp/rq.json")
fail("task-a")               # 任务最终失败：归还全部许可并撤销排队，不占名额
set_priority("task-a", 10)   # 高值先出队，已排队条目立即重排
sweep_expired()              # 显式归还超时许可并补发队列
snapshot()                   # 许可 / 队列 / 每站点持有量 / 事件
```

也可以直接实例化互不干扰的协调器：`QuotaCoordinator(...)`。

## 配额口径

* **两级同时生效**：一张许可必须同时占到站点名额和全局名额才发放。
  站点默认上限 `site_limit`，可按站点覆盖（`site_limits={"pay": 1}`）；
  全局上限 `global_limit`；两者都为 `None` 时该级不限。
* **站点上限**：同一 `site` 同时持有的许可数 ≤ 该站点上限。
  不同站点互不挤占站点名额，但共享全局名额。
* **全局上限**：所有站点同时持有的许可总数 ≤ `global_limit`。
* **名额只算持有的许可**：排队条目、已失败任务、已超时许可都不占名额；
  每次协调（`request` / `release` / `fail` / `sweep_expired` / `snapshot`）
  都会先归还超时许可，再把空出的名额补发给队列。

## 排队口径

* 容量不足时请求进入队列，返回 `granted=False` 和队内位置；之后每次有许可
  归还（释放、失败、超时）都会按队列补发，被补发的任务在
  `granted_from_queue` 中回传。
* 被补发的排队任务有两种取法：调用 `release`/超时的那个任务从
  `granted_from_queue` 看到它；被补发的任务自己下次 `request` 时直接拿到
  这张许可（`reason="granted_from_queue"`，不会重复占用名额）。
  `snapshot()["active"]` 里的 `claimed` 表示该许可是否已被持有者取走；
  未取走的许可同样按租期自动归还。
* **出队顺序**：先按优先级（`priority`，高值优先，默认 0），同优先级按请求
  到达顺序 FIFO。`set_priority` 对已排队条目立即生效；新来的高优先级请求
  也可以插到队首。
* 补发时会跳过站点已满的队首条目，避免被单个站点的排队任务卡住整队。
* 队列不会因为某任务重复释放而错发：一次释放只归还一个许可、只补发一个。
* 同一任务对同一站点重复 `request` 时复用已有排队条目（返回
  `reason="already_queued"`），避免重试循环把队列撑爆。

## 释放口径

* `release(task)` 释放该任务**最早**持有的一张许可；同一任务持有多个许可时
  需多次释放。
* **重复释放幂等**：没有持有许可时返回 `released=False, duplicate=True`，
  不扣减任何名额，也不会误伤已补发的其它许可。
* **过期释放幂等**：许可超过 `lease_seconds` 未释放会在下次协调时自动归还，
  此后再 `release` 返回 `released=False, expired=True`，同样不重复扣减。
* **失败任务不占名额**：`fail(task)` 一次性归还该任务的所有许可并撤销它的
  排队条目，随后立即补发队列；该任务之后可重新 `request` 正常重试。

## 跨进程

设置 `state_path` 后，每次变更都在 `flock` 排他锁下加载-修改-原子写回
（临时文件 + `os.replace`），多进程可共享同一份协调状态：

```bash
python3 -m retryquota /tmp/rq.json            # 缩进 JSON 快照
python3 -m retryquota /tmp/rq.json --events 5 # 只看最近 5 条事件
python3 -c 'from retryquota import read_state; print(read_state("/tmp/rq.json"))'
```

事件流记录 `grant` / `enqueue` / `release` / `release_rejected` / `expire` /
`fail_release` / `cancel`，可直接用于观察许可、排队与释放。

## 默认值

`global_limit=64`、`site_limit=16`、`lease_seconds=300`，可用环境变量
`RETRYQUOTA_GLOBAL_LIMIT` / `RETRYQUOTA_SITE_LIMIT` /
`RETRYQUOTA_LEASE_SECONDS` / `RETRYQUOTA_STATE_PATH` 覆盖。
