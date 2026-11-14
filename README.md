# partitionstats

分区统计与裁剪：登记每分区行数、大小和时间范围，统计带缓存，查询按时间范围裁剪。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 统计口径

- 每个 `.jsonl` 文件是一个分区；登记行数、字节大小、`ts` 字段的最小/最大时间（epoch 秒）。
- `ts` 支持 epoch 数字或 ISO 日期字符串；缺 `ts` 的分区时间范围为 `None`。
- `stats(dataset)` 返回聚合（files/rows/bytes）和每分区明细，结果缓存。
- 写入或删除分区后调用 `invalidate(dataset)`（或 `register(dataset)`），缓存失效、下次重建。

## 裁剪口径

- `prune({"dataset", "start", "end"})`：分区时间范围与过滤区间无交集则跳过，返回 `{"scan", "skip"}` 清单。
- 无时间范围的分区无法裁剪，总是扫描；被扫描分区的访问量 `hits` 加一。
- `query(dataset, filters)` 在裁剪基础上输出扫描分区、跳过分区和耗时秒。
- `hotspots(dataset)` 按访问量、大小降序列出热点分区。
