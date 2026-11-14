# ckptgen

纯标准库的多代际检查点模块。每个任务在 `root/<task>/` 下保存若干代际文件
`generation-N.json`，文件为信封结构 `{"generation", "checksum", "state"}`，
代际号从 0 开始逐次加一，校验和是对 `state` 的 UTF-8 JSON 做 SHA-256。

## 代际与保留口径

- 写入先落临时文件 `generation-N.json.tmp`，`fsync` 后 `os.replace` 原子改名，写到一半崩溃只留下临时文件，旧代际仍可读。
- `save(task, state, keep=5)` 保留最近 5 代；超过后按最旧优先清理，当前正在写入的代际永不参与清理。
- 清理在任务级读写锁保护下进行，正在读取的代际不会被删除；同一任务的并发写由锁串行化（进程内读写锁 + `flock` 进程间锁）。
- `load(task, generation=None)` 校验代际号与 SHA-256；代际损坏或缺失时跳过并回退到上一代，全部不可读抛 `CheckpointError`，每次回退可由 `fallback_records()` 查询。

## 接口与演示

```python
info = save(task, state, root=root, keep=5)  # {generation, retained, removed, path}
state = load(task, generation=None, root=root)
retained_generations(task, root=root)         # 保留列表（旧到新）
current_generation(task, root=root)           # 当前代际号
fallback_records(task=None)                   # 回退记录
```

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
