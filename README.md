# datasetcache

现状：按固定一小时TTL过期，失效时整表清空。

问题：上游更新后要等一小时，批量更新把存储打爆，维表改了事实表也清。

目标：按数据集版本和血缘依赖失效，分批限速，支持预热和旧值兜底。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 版本来源

- 版本由上游元数据提供（调度系统的表版本、分区版本或数据 mtime），缓存层不自行生成。
- `register_dataset(name, version, depends_on)` 登记版本和血缘；`set_version` 只更新版本。
- `invalidate(dataset, version=...)` 传入上游最新版本，与登记版本一致视为无变化。
- `put(..., dataset=...)` 写入时记录当前版本，缺省取登记版本。

## 失效口径

- 版本不变不失效；版本变化只清该数据集及血缘下游（含传递依赖），无关数据集不动。
- 无数据集元数据的条目按未知血缘保守清理；`get` 时也会懒校验版本。
- 未命中原因分 `missing` / `expired` / `version`，`report()` 输出失效条数、回源数、耗时和原因。
- `invalidate(..., batch_size, rate)` 分批限速；`preheat` 预热；`get(..., loader, allow_stale=True)` 回源失败用旧值兜底。
