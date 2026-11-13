# tenantcrawl

多租户采集任务执行层，纯 Python 标准库。共享代码只有执行引擎
（`engine.execute`），配置、队列、结果目录和配额按租户隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 用法

```python
from tenantcrawl import configure, run, read_results, stats

configure("acme", workers=2, quota=500, rate_per_sec=50)
done = run("acme", [{"id": 1}])   # 返回完成任务数
rows = read_results("acme")       # 只能读本租户结果
info = stats("acme")              # 每租户任务数与资源占用
```

## 隔离边界

- 结果目录：`results/<tenant>/out.json`，每租户一个，互不可见。
- `read_results(tenant, name)` 只读本租户目录，路径穿越抛 `TenantAccessError`。
- 队列、配额、限速器均为每租户独立实例，一个租户不会拖慢另一个。
- `read_all()` 是运维聚合视图，不属于租户侧 API。
- 新增租户无需复制代码：`configure` 登记或直接用默认配置 `run`。

## 配置结构

- `configure(tenant, workers=4, quota=1000, rate_per_sec=100)`，未配置的项用默认值。
- `workers`：每租户并发线程数，整数 >= 1，默认 4。
- `quota`：单次 `run` 允许的最大任务数，整数 >= 0，默认 1000。
- `rate_per_sec`：每租户限速（任务/秒），> 0，默认 100。
- 租户名：`^[A-Za-z0-9][A-Za-z0-9._-]*$` 且不含 `..`，非法抛 `InvalidTenantError`。

## 错误

异常路径全部显式抛出，不静默跳过：

- `InvalidTenantError`：租户名为空、含路径分隔符或 `..`。
- `InvalidTaskError`：任务列表不可迭代，或任务不是可 JSON 序列化的 dict。
- `QuotaExceededError`：单次 `run` 的任务数超出租户配额。
- `TenantAccessError`：读取路径越出本租户结果目录。
