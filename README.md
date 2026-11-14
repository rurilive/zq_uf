# dedupwindow

带水位和滑动窗口的有界流式去重，纯 Python 标准库。

## 水位与窗口口径

- `seen(key, ts)` 按事件时间判重，重复返回 True，计入 hits。
- `advance(watermark)` 推进水位（只前进），清理 `ts < watermark - window_size` 的状态，返回剩余大小。
- 迟到数据：`watermark - allowed_lateness <= ts < watermark` 仍可判重，计入 late。
- 超出允许范围：`ts < watermark - allowed_lateness` 标记过期，计入 expired，返回 False。
- 状态超过 `max_size` 时按最旧 ts 淘汰，计入 dropped。
- `stats()` 输出 hits / late / expired / dropped / size / watermark。
- `dumps()` / `loads()` 序列化状态，重启后恢复水位和窗口。
- `configure(window_size, allowed_lateness, max_size)` 调整参数；`reset()` 恢复默认并清空。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
