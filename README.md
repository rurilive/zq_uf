# uafilter

按站点适配的请求头池：每站点 UA 列表 + 备用头，封禁自动切换并冷却。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 请求头口径

- `headers(site)` 返回当前头：User-Agent 取站点 UA 列表当前项。
- Accept-Language / Accept-Encoding 按站点配置，缺省回退全局配置。
- 未配置的站点整体回退全局 UA 列表与语言、编码。

## 切换口径

- 封禁识别：429 立即切换；403 需连续 3 次或响应含 captcha/access denied 等特征，避免误判。
- 切换时旧 UA 进入 300s 冷却，选下一个不在冷却的备用 UA；全部冷却则维持当前。
- 每次切换记录 from/to/原因/时间；`stats(site)` 输出当前头、切换记录和封禁统计。
- `report(site, status, response=None)` 上报状态码（可选响应文本），返回全局状态码统计。
