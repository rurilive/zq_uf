# pagetrack

采集页面版本追踪：保存生成序号和指纹，内容未变不新增版本，订阅方按序号拉增量。

口径：
- `save(page, content)`：指纹为内容 sha256；返回的 `version`/`fingerprint` 是被取代的旧值（首次收录或内容未变时为 `None`），当前值看 `version_no`/`digest`/`seq`；内容未变返回 `created=False`，不新增版本。
- 首次收录不算变更；内容变化或回滚才进变更日志，每条带全局序号 `seq`，增量采集按序号续抓。
- `changes(since, site=, start=, end=)`：按序号续拉，可按站点和时间过滤，`cursor` 是下次续点。
- `subscribe(site)`/`pull(sub)`：订阅游标持久，断线后用同一订阅 id 续拉；`pull(sub, since=n)` 可回放。
- `versions(page)` 输出全部历史版本；`rollback(page, version)` 回滚生成新版本，历史记录保留。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
