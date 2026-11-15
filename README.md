# crawlmerge

多分片采集结果的合并模块，纯 Python 标准库。按主键（`id`）与版本（`version`，缺省为 0）合并，接口保持 `merge(shards)`、`resume(token)`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 合并口径

- 同主键版本高者优先；低版本行的有效值只补高版本行的空字段，不覆盖。
- `None` 为空值，空值永不覆盖有效值；0、False、空串均算有效值。
- 同版本且同一非空字段取值不同才算冲突：主表后到先写，冲突区保留两侧原始行，按主键聚合。
- 分片写成 `{"shard": 分片号, "rows": [...]}`；裸列表按进程内序号自动编号。
- 分片先按分片号排序再合并；返回 `rows`（合并后主键数）、`processed`（本次实处理行数）、`conflicts`、`checkpoint`/`token`。

## 断点口径

- 断点文件按批次命名：`<state_dir>/checkpoint_<batch>.json`，默认目录是系统临时目录下的 `crawlmerge/`，批次默认 `default`。
- 清单按分片号排序，每条记录分片号、已合并位置 `position`、总行数 `rows` 与内容指纹；每个分片边界落盘，失败时也会保留分片内已处理位置。
- 失败后用 `resume(token)` 查看进度，再以相同 `batch` 调 `merge(shards)`，已合并位置自动跳过。
- 重复分片按清单位置跳过、冲突双侧去重，重跑结果与一次跑完一致，即幂等。
- 无 token 的 `resume()` 没有明确恢复目标，返回零值进度；跨进程续合请持久化并回传 `token`。
