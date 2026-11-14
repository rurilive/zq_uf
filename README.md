# partcompact

现状：合并时先删旧文件再写新文件，没有清单。

问题：合并期间查询读不到，失败留半份，并发合并踩踏。

目标：读旧写新再原子切换，失败保留旧文件，重复合并幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 切换口径

- 合并全程持有分区锁文件，新数据先写临时文件，再 rename 成 merged-<checksum>.jsonl。
- 校验通过后才用 os.replace 原子切换 manifest.json，切换前 query 读旧清单或旧分片。
- 切换失败自动回退旧清单；任一阶段失败都清理临时文件、保留旧文件。
- 切换成功后删除旧分片与旧合并文件；内容一致的重复合并直接跳过（幂等）。

## 校验口径

- 写入时统计行数，flush + fsync 落盘后回读临时文件，重算行数与 sha256。
- 回读行数与写入行数不一致即判定失败，放弃切换并删除临时文件。
- 清单记录 file / rows / checksum 三项，是切换与幂等判断的唯一依据。
- compact 返回合并前后文件数、行数、校验和与 switched 切换结果。
