# sitemapinc2

站点地图分片增量合并，纯 Python 标准库。

## 合并口径

- `merge(old, new)` 是 lastmod 合并原语：同 URL 比较 lastmod，新值覆盖旧值，
  相同或更旧记 `skipped`；结果只含 `new` 覆盖到的 URL，`old` 独有的 URL
  不带入结果，原语层不判删（单分片无法区分“上游删除”和“属于别的分片”）。
- `IncrementalIndex` 负责分片层：每个序号分片存最近一次下载成功的内容；
  同序号分片重下后消失的 URL 才确认删除，写墓碑并从在线索引剔除。
- 分片按序号折叠（乱序自动重排），同 URL 跨分片取最大 lastmod；重复应用
  同一分片内容幂等（全部记 `skipped`，无新增/更新/删除）。
- 增量结果始终等于 `rebuild()`：按序号并集折叠全部分片，再剔除存活墓碑。
- 输出四类差异：`added` / `updated` / `deleted` / `skipped`，`diff()` 给 URL 清单。

## 墓碑口径

- 墓碑结构为 `url -> (删除时 lastmod, 删除时间戳)`，URL 重新出现即清除墓碑
  并记 `added`（复活）。
- 保留时间由 `IncrementalIndex(tombstone_ttl=秒)` 配置，默认 7 天，
  `None` 表示永久保留；`cleanup()` 清掉过期墓碑，TTL 内墓碑持续压制同名 URL。
- 分片下载失败传 `None`：旧分片内容与整个索引原样保留，结果带 `failed`
  字段，之后可单独重下该分片。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
