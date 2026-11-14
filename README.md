# partitionmove

现状：双写 + 位点 + 原子切换清单，失败自动回滚，旧路径保留。

## 阶段

`prepare` 记录源快照 → `copy` 复制到目标并把位点推进到已复制行数 → `catchup`
期间写入双写旧/新路径，按位点补齐 → `validate` 校验行数与校验和 → `switch`
原子替换清单（tmp + fsync + `os.replace`）→ `done` 删除 journal。

## 回滚口径

切换前失败：旧路径从未删除，按 journal 中的源快照校验前缀后回到原路径。
切换后失败：`move()` 自动按清单把 `active_path` 改回源并校验；也可随时调用
`rollback()` / `rollback(partition=...)` 按清单恢复并校验。切换后若有写入只
落在新路径，回滚会把这段尾部补回旧路径后再校验（`verification.replayed`）。
目标路径数据保留，校验不通过时 `ok=False` 并保留现场。无迁移可回滚时保持原
接口抛 `NotImplementedError`。

## 用法

```bash
python3 demo.py                          # 演示双写、切换失败自动回滚、手动回滚
python3 -m unittest discover -s tests    # 15 个用例
```

```python
from partitionmove import move, rollback, write, read, seed

seed("dt=1", [{"id": 1}], path="hot")    # 测试/演示初始化源数据
move("dt=1", "cold")                     # 双写、校验、原子切换
write("dt=1", {"id": 2})                 # 迁移期间双写并推进位点
rollback(partition="dt=1")               # 按清单恢复旧路径并校验
```

## 落盘

`state_dir`（默认临时目录，可用 `PARTITIONMOVE_STATE_DIR` 或 `state_dir=` 指定）：

- `manifest.json`：切换清单，原子替换，记录 active_path / 行数 / 校验和
- `journal/<hash>.json`：迁移阶段、位点、源/目标快照
- `data.json`：各路径的分区数据，旧路径不删除

实现与回滚口径留档见 [docs/migration-design.md](docs/migration-design.md)。
