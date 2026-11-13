# linkfollow

纯 Python 标准库的站内链接发现，接口保持 `discover(page, depth)`。
`page` 可传 HTML 字符串，或带 `html/url/site_host` 的 `Page`（推荐，
URL 用于补全相对链接、作为来源和同站判定）。返回 `DiscoverResult`：
列表本体是新页面 `Link`，另有 `pagination`（翻页链接）和 `rejected`
（被拒链接及原因）。每个 `Link` 带 `dedup_key`、`source`、`depth`。

## 规范化规则

- 相对链接按页面 URL 补全为绝对链接，去掉锚点（`#fragment`）。
- 删除会话参数：`sid`、`sessionid`、`session_id`、`phpsessid`、
  `jsessionid` 及 `aspsessionid*`；其余查询参数保留并按字母排序。
- 去重键为 `scheme://host/路径`（忽略查询串，末尾 `/` 视为相同），
  同一路径同页只保留第一次出现的链接，重复项进 `rejected`。
- 外站（host 与 `site_host` 不同）默认不入队，记 `external`；
  传 `allow_external=True` 可放开。

## 翻页与上限

- 翻页识别：查询参数 `page/p/pn/pageno`、路径 `/page/N` 或 `/p/N`、
  锚文本为下一页/上一页/首页/尾页/第 N 页/next/prev/more 等。
- 翻页链接归并到 `pagination`，`parent` 指向来源页面，深度沿用父页面，
  不单独入队、不单独计深度。
- `max_depth`（默认 5）：页面在 `depth >= max_depth` 时不再产出新页面，
  记 `depth_limit`；`max_per_level`（默认 100）限制每层新链接数，
  超限记 `per_level_limit`。
- 其他拒绝原因：`duplicate`（同路径重复）、`external`（外站）、
  `unsupported_scheme`（非 http/https，如 mailto）。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
