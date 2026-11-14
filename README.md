# taskfair

纯 Python 标准库实现的多队列公平调度器：按权重轮转、低优队列保底配额、
高优任务抢占续跑，并输出各队列的出队与等待统计。

## 快速开始

```python
from taskfair import configure, submit, next, preempt, complete, stats

configure("big", weight=3, guarantee=0, priority=0)
configure("small", weight=1, guarantee=1, priority=0)
submit("big", {"id": "b0"})
submit("small", {"id": "s0"})
task = next()          # 等价于 next_task()，同队列内严格 FIFO
complete(task["id"])   # 执行完释放执行位
print(stats())
```

## 口径说明

- **权重 weight（正整数，默认 1）**：一个调度轮次内，队列最多出队
  weight 个任务；轮次内所有队列的权重份额耗尽后自动开新一轮。
  多个队列同权重时，按队头任务等待时间最久者优先（再按提交序号），
  因此默认配置下仍是全局 FIFO，兼容旧行为。
- **保底 guarantee（非负整数，按队列配置，默认 0）**：每轮先于权重
  份额发放，只要队列有积压，每轮至少出队 guarantee 个任务，与权重
  份额叠加；保底名额同样按等待时间在积压队列间排序。低优队列配置
  guarantee >= 1 后，任意一轮都不会被高权重队列完全饿死。
- **优先级 priority（非负整数，默认 0）**：只用于抢占，不影响正常
  出队顺序。`preempt()` 不传任务 id 时，抢占在执行任务中优先级最低
  的一个。
- **抢占与续跑**：`preempt(task_id, progress=...)` 把任务连同 progress
  保存到任务的 `progress` 字段，放回原队列队头并累计该队列抢占次数；
  下次 `next()` 优先返回它，执行方凭 progress 从断点续跑。出队返回的
  task 不注入 preempted 等内部字段。
- **统计 stats()**：每个队列返回 submitted/dequeued/waiting/running、
  total_wait、avg_wait、max_wait（单位与注入时钟一致，默认秒）和
  preemptions；等待时长按每次出队时刻减去入队时刻累计，抢占重入队
  后只统计重新等待的部分。

## 异常路径

所有错误抛 `taskfair.SchedulerError`：weight 非正整数、guarantee/
priority 为负、queue 不是非空字符串、task 不是映射或缺少非空 id、
抢占/完成不在执行中的任务、快照版本不符或结构损坏。

## 序列化与重启

```python
from taskfair import snapshot, restore
import json

data = snapshot()                 # 纯 dict/JSON 可序列化的完整状态
restore(json.loads(json.dumps(data)))  # 重启后恢复队列、进度和统计
```

需要多个独立调度器或注入可控时钟（测试/演示）时，直接使用
`taskfair.Scheduler(clock=...)`，模块级函数操作其中的默认实例，
`reset()` 可将默认实例还原为空。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

