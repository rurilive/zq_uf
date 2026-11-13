# siteprobe

新站点接入前的结构化探测工具，仅依赖 Python 标准库。对首页、列表页和多条详情页做并发探测，
单站点限速，输出可落盘、可跨版本对比的 JSON 报告，并按可接入、需登录、需人工三类打分。

## 用法

```python
from siteprobe import Samples, probe, save_report, load_report, diff_reports

report = probe("https://example.com/", Samples(
    list_urls=["https://example.com/news"],
    detail_count=3,      # 每个列表最多探测的详情条数
    max_workers=4,       # 并发线程数
    rate_per_sec=2.0,    # 单站点每秒最大请求数，0 表示不限速
    timeout=10.0,
))
save_report(report, "reports/example.json")

old = load_report("reports/example.json")
diff = diff_reports(old, probe("https://example.com/", Samples(...)))  # 站点改版对比
```

旧接口保持不变：`probe(url, fetch_callable)` 仍返回 `url/status/bytes/content_type` 四个字段，
便于存量调用与 `tests/test_probe.py` 中的断言继续工作。自定义抓取器签名为 `fetcher(url, timeout=...)`，
返回 `status/headers/body/url/redirects/elapsed/error`；不接受 `timeout` 关键字的旧抓取器也可直接使用。

```bash
python3 demo.py                      # 本地起服务，演示三类站点与改版对比
python3 -m unittest discover -s tests
```

## 报告字段

顶层结构：

| 字段 | 含义 |
| --- | --- |
| `report_version` | 报告结构版本号，当前为 `1` |
| `url` / `generated_at` | 站点入口与报告生成时间（UTC） |
| `rate_limit` | 限速配置与实际发出的请求数 `requests` |
| `pagination` | 分页推断结果 |
| `details` | 详情链接模式、采样 URL 与候选总数 |
| `signals` | 登录、验证码、签名三类风险特征及证据 |
| `assessment` | 分数、分类、置信度、扣分明细与证据 |
| `pages` | 每个被探测页面的完整记录 |

`pagination`：

- `style`：`query`（查询参数式）、`path`（路径式）或 `none`（未识别）。
- `page_param` / `path_pattern`：分页参数名（如 `page`）或路径模板（如 `/news/list_{page}.html`）。
- `next_selector` / `next_href`：下一页 CSS 选择器（优先链接 class）与实际链接。
- `total_pages`：从分页链接中观测到的最大页码。
- `evidence`：每条推断结论对应的可追溯证据。

`pages[]`：

- 基础信息：`url`、`kind`（`home`/`list`/`detail`）、`status`、`bytes`、`content_type`、`elapsed`。
- 编码：`encoding`（按 HTTP 头 → meta → BOM → 内容兜底推断，GBK/GB2312 归一到 `gb18030`）、
  `garbled_ratio`（替换符与典型乱码字符占可见字符比例）、`garbled`（比例 ≥ 0.05 或含替换符）。
- 重定向：`final_url`、`redirect_count`、`redirects`（逐跳 `status` + `url`，超时同样保留已发生的链）。
- 异常：`timed_out`、`error`（`timeout`、`connection_error: ...`、`http_4xx/5xx` 等）。

`signals`：

- `login_required`：重定向到登录路径、跨域登录页，或页面出现“请先登录”等文案。
- `captcha`：页面或链接出现 `captcha`/`recaptcha`/`geetest`、验证码图片、滑块/拼图文案。
- `signature`：URL 或脚本中出现 `sign`、`signature`、`token`、`timestamp`、`nonce`、`app_key` 等字段。

## 打分规则

基础分 `100`，按命中问题扣分（同类扣分设上限）：

| 扣分项 | 分值 |
| --- | --- |
| 页面请求超时 | 每页 15，上限 45 |
| 页面连接失败 | 每页 15，上限 45 |
| 非 200 状态码 | 每页 10，上限 30 |
| 重定向链 ≥ 3 跳 | 5 |
| 存在乱码页面 | 20 |
| 未推断出分页规则 | 10 |
| 检测到登录要求 | 40 |
| 检测到验证码 | 25 |
| 检测到接口签名特征 | 20 |

分类优先级：

- `login_required`：命中登录特征（即使同时存在其他风险，仍先归入此类）。
- `manual_review`：命中验证码或签名、存在乱码页面，或最终分数低于 60。
- `accessible`：其余情况。

置信度取 `0~0.98` 的数值与 `high/medium/low` 标签：由页面成功率（权重最高）、
风险信号命中数、分页是否可推断、是否存在乱码共同决定；阈值为 `≥0.85 high`、`≥0.6 medium`。

## 报告对比

`diff_reports(old, new)` 忽略时间戳与耗时抖动，输出：

- `classification_changed`：分类、分数、置信度变化。
- `pagination_changed`：分页样式、参数、下一页选择器、总页数变化。
- `signals_changed`：三类风险特征证据的新增/消失。
- `pages_changed` / `pages_added` / `pages_removed`：页面级状态码、字节数、重定向、编码、乱码比例变化。
- `changed`：任一维度存在差异时为 `true`。
