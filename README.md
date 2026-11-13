# formfill

纯 Python 标准库的批量报销单填报工具（`concurrent.futures` 线程池 + 本地 JSON 回执），
接口保持 `submit_batch(rows, workers)`，返回可按输入顺序迭代的 `BatchResult`。

## 幂等键与回执口径

- 每条行先由模板算业务键：默认取 `id` 列；模板可声明多列，按顺序拼成 `公司:月份:单号`。
- 每次提交结果以业务键为键写入 `ReceiptStore`（内存或本地 JSON 文件，原子替换落盘），
  内容含状态、回执号、弹窗类型、校验失败的字段名与原值。
- 重试前按回执核对（`reconcile`）：
  - 已有成功回执（`ok/success/accepted/confirmed`）→ 跳过，不再调用提交函数；
  - 明确失败（`rejected/failed/validation_error/error`）→ 终态可安全重放；
  - 状态不明（`timeout/transient/unknown` 等）→ 进待人工；传入 `verifier` 时先向远端核对，
    远端确认成功则补登回执并跳过，避免超时盲重试开出重复单据；传输层异常同样不自动重试。

## 并发口径

- 批内用 `ThreadPoolExecutor(max_workers=workers)` 并发，回执清单按输入顺序排列。
- 跨批次共享同一个 `KeyCoordinator` 时，每个业务键一把锁：同一键全局只允许一个提交在跑，
  冲突调用阻塞进等待队列（`coordinator.stats()` 可查在跑键、排队数和累计等待次数），
  等前序提交落回执后再走核对逻辑，因此重复行表现为一次成功 + 一次跳过。
- 单条异常被隔离记录，不会拖住整批；最终输出成功、跳过、失败、待人工四份清单，
  `BatchResult.report()` 给出可直接 JSON 序列化的完整报告。

## 模板：日期、金额、精度、时区

`Template` 按模板声明转换，避免不同 CSV 模板列错位：

- `key_fields`：业务键列；`field_map`：标准字段（`date/amount/note`）到模板实际列名的映射。
- `date_input_formats`：`iso`（含旧版非补零 `2026-3-1`）或一组 `strptime` 格式，按序匹配；
  `date_output_format`：`iso` 或 `strftime` 输出格式。
- `timezone`（默认 `UTC`）：时间戳与无时区日期时间按此时区解释，跨时区日期用 `zoneinfo` 换算。
- `amount_precision`（默认 2 位）、`amount_rounding`（`half_up`/`half_even`，默认 `half_up`）、
  `amount_as_decimal`（`True` 时返回 `Decimal`，消除浮点误差）；自动去掉千分位逗号。
- 转换失败抛 `FieldFormatError`，批量流程捕获后把字段名、原值、原因写入失败清单并继续后续行。

## 弹窗处理

提交函数可以抛 `DialogBlocked(type, message, fields)`，也可以返回
`{"dialog": {"type", "message", "fields"}}` 或 `{"status": "dialog", ...}`。
`DialogPolicy` 按类型决策：

- `confirm/warning`：自动确认，载荷带 `_dialog_confirmed` 重放一次（次数上限可配）；
- `validation/error`：自动取消，字段名与原值进失败清单，不重放；
- 未知类型或确认超上限：进待人工清单。也可传自定义 `handler` 覆盖决策。

## 干跑预览

`submit_batch(rows, workers, dry_run=True)` 只做业务键计算、模板转换和回执命中检查，
不调用提交函数、不写回执：可提交的行进 `preview`，命中已有成功回执的行进 `skipped`，
格式错误仍进 `failed`。

## 用法

```python
from formfill import KeyCoordinator, ReceiptStore, submit_batch
from formfill.template import Template

template = Template(
    key_fields=("company", "month", "id"),
    date_input_formats=("%Y/%m/%d",),
    amount_precision=2,
    amount_rounding="half_up",
    timezone="Asia/Hong_Kong",
    field_map={"date": "报销日期", "amount": "金额(元)", "note": "说明"},
)
store = ReceiptStore("receipts.json")  # 不传路径则仅内存
coordinator = KeyCoordinator()

result = submit_batch(rows, workers=8, submit_one=submit_one,
                      template=template, store=store, coordinator=coordinator)
print(result.counts())          # success/skipped/failed/manual/preview 计数
for receipt in result:          # 兼容旧版：按输入顺序迭代回执
    print(receipt.business_key, receipt.status, receipt.message)
```

旧签名 `submit_batch(rows, submit_one)` 与 `normalize_row(row)` 保持兼容。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

`demo.py` 用两个线程并发跑两批交叉数据（含重复业务键、确认弹窗、校验拒绝、坏金额），
再整批重试演示成功跳过、失败重放。
