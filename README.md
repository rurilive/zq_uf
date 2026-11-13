# pagedigest

纯标准库的商品分页抓取工具，入口仍是 `fetch_all`，返回 `Item` 列表，结构不变。

## 游标抓取

`fetch_page(cursor)` 接收游标而不是页码：首传 `initial_cursor`（默认 `1`，新接口建议传 `None`），
下一页游标从响应的 `next_cursor` 字段取（字段名可用 `cursor_field` 配置）。响应无游标字段时
自动退回页码 +1，兼容老接口。`items` 为空数组视为正常结束，不再当失败。

## 断点格式

配置 `checkpoint_path` 后，每页成功即原子写入本地 JSON（先写 `.tmp` 再 `os.replace`）：

```json
{
  "version": 1,
  "cursor": "c2",
  "done": false,
  "updated_at": "2026-11-13T10:00:00+00:00",
  "requests": 2,
  "upserted": 2,
  "items": [{"item_id": "1", "title": "键盘", "price": "299.00", "url": "http://shop/p/1"}]
}
```

重启时读取该文件，从 `cursor` 继续；`done: true` 表示已抓到空页，直接返回 `items`，不再请求网络。
中断时保留最后一次成功页之后的游标，重复执行不会丢数据或重复入库。

## 限速与退避

`rate_interval` 控制请求间隔（秒，默认 0 即不限速）。请求抛出带 `code/status_code/status == 429`
的异常（如 `urllib.error.HTTPError`）时，按 `backoff_start` × `backoff_factor` 指数退避重试，
封顶 `backoff_max`，最多 `max_retries` 次；响应带 `Retry-After`（秒）时优先采用。

## 去重与缺失字段

商品 URL 先剔除 `utm_*`、`spm`、`ref` 等追踪参数（`tracking_params` 可配置），再取 SHA-1
前 16 位作为指纹；同指纹商品覆盖更新（价格等字段以最新页为准），不新增记录。
缺少 `id` 或 `url` 的记录记一条 JSONL 样本（`invalid_sample_path`）后跳过，不整批失败：

```json
{"cursor": "c1", "ts": "...", "raw": {...}, "reason": "missing_required_field", "missing_fields": ["url"]}
```

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
