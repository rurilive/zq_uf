# replayfail

失败样本的回归回放工具，纯 Python 标准库。样本保存完整请求与响应摘要，
回放时按原请求重建调用、对比新旧错误类型，并把结果写入独立演练库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 记录样本

```python
record(failure, path)
```

`record` 把一次失败请求规范化后追加为一行 JSONL，字段如下：

- `id`：由方法、URL、请求头、请求体算出的稳定标识，用于演练库去重。
- `method`：请求方法，缺省为 `GET`，统一转大写。
- `url`：请求 URL。
- `headers`：请求头字典（字符串键值），原样保留，回放时用于重建签名等。
- `body`：请求体，字符串形式；字节按 UTF-8 解码，其他对象序列化为 JSON。
- `error_type`：采集时的错误类型，缺省为 `unknown`。
- `response`：响应摘要，含 `status`、`headers`、`body_summary`（截断到 500 字符）。
- `recorded_at`：记录时间（UTC，`YYYY-MM-DDTHH:MM:SSZ`）。

## 回放

```python
replay(paths, fetch, drill_db=".replayfail/drill.sqlite3")
```

- `fetch` 收到按样本重建的请求字典：`method`、`url`、`headers`、`body`；
  单参数函数仍按旧约定调用 `fetch(url)`，兼容历史用法。
- 返回值约定：`None` 或真值表示成功；字符串表示新错误类型；字典读取
  `error_type` 字段；`fetch` 抛异常时取异常类名作为新错误类型。
- 回放只读取样本文件，并写入 `drill_db` 这个独立 SQLite 演练库，
  不修改样本、不碰正式数据；换库即换一套演练数据。
- 演练库按样本 `id` 做 upsert：同一批样本重复回放只更新分类和
  `replay_count`，不会重复入库。

## 结果分类

`replay` 返回三类样本计数：

- `fixed`：回放成功（`fetch` 返回 `None`/真值），原错误已修复。
- `still_failed`：仍失败，且新错误类型与原 `error_type` 相同。
- `error_changed`：仍失败，但错误类型变成了别的错误。
