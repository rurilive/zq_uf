# qualitygate

数据质量分级门禁，纯 Python 标准库。接口保持 `check(batch)` 与
`waive(rule, until, reason)` 两个入口，另提供 `audit()` 查看豁免审计。

## 分级口径

规则通过 `level` 声明三级，缺省（未填或无法识别）按 `block` 处理，保持
历史一票否决口径：

- `block` 阻断级：失败即阻断，与失败行数无关。
- `warning` 告警级：默认不阻断；当失败行数 `failed_rows >= 1000`，或
  影响范围 `failed_rows / total_rows >= 0.01` 时升级为阻断。两个阈值可在
  批次里通过 `thresholds: {"warning_rows", "warning_ratio"}` 覆盖。
- `info` 提示级：只计数，永不阻断。

规则结构：`name`、`failed`、`level`、`failed_rows`（缺省按 1 计）、
`total_rows`（可选，用于计算影响范围）。

`check(batch)` 返回：

- `blocked`：最终阻断结论。
- `failed`：未被豁免的失败规则名（按批次顺序）。
- `counts`：各级未豁免失败数，键为 `block` / `warning` / `info`。
- `waived`：本次被生效豁免挡掉的失败规则名。
- `active_waivers`：本次实际生效的豁免，含 `until` 与 `reason`。

同一批次重复调用 `check` 结果一致：结果不含时间戳，过期清扫幂等。

## 豁免口径

`waive(rule, until, reason)` 按规则名登记豁免：

- `until` 为到期时间的 epoch 秒值，`reason` 必填，说明豁免原因。
- `until` 在未来：豁免生效；`check` 时对应失败规则不计入 `failed` 与
  `counts`，改记入 `waived` / `active_waivers`，且不参与阻断。
- `until` 已过：豁免立即失效（`active=False`），登记仍留痕。
- 到期自动失效：后续 `check` 会清扫过期豁免并追加 `expired` 审计，同一
  豁免只记一次；登记时已过期则当场记 `expired`。
- 审计事件（`waived` / `expired`，含时间、到期点与原因）通过 `audit()`
  读取，只追加、不进 `check` 结果，因此不破坏重复检查一致性。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
