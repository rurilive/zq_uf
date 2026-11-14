# 双写 + 原子切换改造留档

日期：2026-11-15

## 问题

改造前 `move()` 只改内存指向，没有数据搬迁、校验和回滚，迁移必须停写，
路径切换非原子，失败后旧路径已被删除。

## 改造口径

接口保持 `move(partition, target)` 与 `rollback()`：

- `move(partition, target, *, state_dir=None, hooks=None)`：按
  `prepare -> copy -> catchup -> validate -> switch -> done` 推进，返回阶段列表、
  位点 `cursor`、校验结果 `validation`、切换结果 `switch`，失败时返回
  `phase="rollback"`、`rolled_back=True` 和 `rollback` 明细。
- `rollback(*, state_dir=None, partition=None)`：指定分区时按清单/journal 回滚；
  不指定时回滚当前会话可见的迁移；无对象可回滚时保持旧行为抛
  `NotImplementedError`（旧测试未改）。
- `write()`：迁移期间双写旧/新路径并推进 journal 位点；`read()` / `rows_at()`
  分别读活跃路径和指定路径。

## 关键机制

1. 双写与位点：`write()` 在 journal 活跃期间同时写 source 和 target，位点取
   已写入 target 的行数，并原子落盘到 `journal/<hash>.json`。
2. 原子切换：`manifest.json` 通过 tmp 文件 + `fsync` + `os.replace` 整体替换，
   读者只会看到完整旧清单或完整新清单。
3. 切换前校验：validate 阶段和 switch 阶段写清单前各校验一次行数与
   SHA-256 校验和，不一致抛 `ValidationError`。
4. 失败回滚：validate/switch 失败在 `move()` 内自动回滚；清单改回
   `active_path=source`、`state=rolled_back`；旧路径不删除，按 source 快照
   校验（前缀口径，允许迁移期追加）。
5. 切换后回滚：把只落在新路径的尾部补回旧路径后再校验（
   `verification.replayed` 记录补回行数），避免切换后已确认写入丢失。

## 落盘文件

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 切换清单：active_path、migration_id、源/目标行数与校验和、state |
| `journal/<hash>.json` | 迁移阶段、位点、源/目标快照；done/回滚后删除 |
| `data.json` | 各路径的分区行数据；旧路径切换后仍保留 |

`state_dir` 可用参数指定，或用 `PARTITIONMOVE_STATE_DIR`，默认临时目录。

## 验证

`python3 -m unittest discover -s tests`：15 个用例，覆盖旧接口断言、双写与位点、
原子切换、校验失败回滚、切换失败自动回滚、按清单/ journal 回滚、切换后写入
补回。`python3 demo.py` 演示切换失败自动回滚和成功迁移后手动回滚。
