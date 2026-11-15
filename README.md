# tableversion

表版本与回滚模块，纯 Python 标准库。每次提交生成不可覆盖的版本，回滚先校验版本链与数据校验和，再恢复数据。

## 接口

```python
from tableversion import TableVersion

tv = TableVersion(root="./.tvstore", committer="alice")  # 不传 root 则用内存存储
tv.commit(table)            # table = {"name": str, "rows": list, "schema"?: list}
tv.rollback(version, table="orders")        # 单表回滚
tv.rollback(version, tables=["a", "b"])     # 部分表回滚
tv.history("orders")        # 版本链（新→旧），含父版本、提交人、校验和
tv.validate("orders")       # 完整性校验，失败抛 IntegrityError
tv.archive(keep=2)          # 归档过期版本，每表保留最近 keep 个
tv.head("orders")           # 当前版本的数据与元数据
tv.close()
```

模块级门面保持历史接口 `commit(table)` / `rollback(version)` 不变。

## 版本口径

- 版本按表独立编号，整数递增、从 1 开始；**空提交（rows 为空）不生成版本**，返回 `version=None`。
- 每个版本的元数据：`version`、`parent`（父版本）、`committer`（提交人，默认取系统用户，可显式传入）、`timestamp`、`schema`、`schema_changed`、`checksum`、`rows`。
- 校验和 = 行数据规范 JSON（键排序、无空白）的 SHA-256，随版本返回。
- 结构变更与版本绑定：未显式给 `schema` 时从行数据推断列；提交时记录列的 `added` / `removed` / `changed`，无变化则为空字典。
- 版本链从 head 沿 `parent` 回溯，链视图随每次提交/回滚返回。
- 元数据与数据分开存储：文件后端为 `metadata/<表>/vNNNNNN.json` 与 `data/<表>/vNNNNNN.json`；内存后端用独立字典。行快照经 JSON 深拷贝，调用方后续改数据不影响历史版本。

## 回滚口径

- 回滚前先校验整条存活版本链：版本号连续、父指针正确、每个版本的数据重新计算 SHA-256 与元数据一致；任一不通过抛 `IntegrityError`，**不写任何恢复数据**。
- 校验通过后，把目标版本的数据与结构恢复为当前内容，方式是追加一个新的“回滚恢复点”版本（带 `rollback_of` 标记），**历史版本与当前版本全部保留**，链继续向前延伸。
- 不指定表时默认最近一次提交的表；目标版本不存在时返回 `rolled_back=False` 并在 `tables[].reason` 说明原因。
- 部分表回滚：`tables=[...]` 只恢复列出的表，逐表返回结果；先全部校验、再统一恢复。
- 已归档版本不能作为回滚目标，会报 `IntegrityError`。

## 并发与归档

- 所有提交/回滚/归档进入同一条 FIFO 队列，由单一工作线程串行执行；并发提交严格按到达顺序取得连续版本号，不会互相覆盖。
- `archive(keep=N)` 把每表最旧的过期版本移到 `archive/` 树（元数据、数据分别归档），存活链在归档边界终止；`list_archived()` 可查看归档版本。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
