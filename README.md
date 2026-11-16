# irplaybook

安全方向的演练阶段编排小工具：阶段登记目标、执行人和开始时间，带依赖的按序推进；
阶段失败、回退和叫停都留证；叫停后已获权限逐项回收，全部回收演练才算结束。

## 接口

- `plan(exercise)`：登记演练与阶段（`name`、`goal`、`owner`、`after`），返回阶段状态、
  负责人、权限和回收结果；`after` 引用未知阶段或成环直接报 `PlaybookError`。
- `start(exercise, stage, owner=None, at=None)`：前置全部 `done` 才开始；否则返回
  `status="blocked"` 与 `blocked_by`，阶段保持 `pending`。
- `complete(exercise, stage, result=None, at=None)`：记录结果；**不会**自动开始后续阶段。
- `fail(exercise, stage, reason, at=None)`：记录失败原因和失败当时的权限快照，
  后续阶段不自动开始，依赖它的阶段继续被 `blocked`。
- `rollback(exercise, stage, reason, at=None)`：阶段退回 `pending` 并记录原因；
  后续阶段已推进时拒绝回退，需先回退后续阶段。
- `acquire(exercise, stage, asset, detail=None, at=None)`：登记阶段执行中拿到的权限。
- `halt(exercise, reason, by=None, at=None)`：由演练负责人发起；停掉执行中的阶段，
  把已获权限列入回收清单。演练有 `owner` 时 `by` 必须与其一致。
- `recover(exercise, asset, ok=True, note=None, by=None, at=None)`：逐项确认回收；
  `ok=False` 必须写明卡点，权限记为 `unrecovered` 并单独列出，可稍后重试。
- `timeline(exercise)`：按发生顺序返回事件（`seq`/`at`/`type`/`stage`/`owner`/`detail`），
  附阶段状态、已获权限、回收结果和执行人进度，复盘直接取用。
- `progress(exercise)`：负责人按演练查看各执行人的阶段进度。

## 状态口径

- 阶段：`pending` → `running` → `done` / `failed`；叫停时执行中的转 `stopped`。
- 演练：`planned` → `running` → `finished`（阶段全部完成）/ `failed`（有阶段失败）/
  `halted`（叫停待回收）→ `closed`（权限全部回收）。
- 时间参数可传 `datetime`（naive 按 UTC）或 ISO 字符串；不传则取当前时间。
  返回的所有阶段和权限都是副本，改动不会影响内部记录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
