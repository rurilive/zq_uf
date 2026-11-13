# sitemaptool

纯 Python 标准库的 sitemap 入口整理工具，入口函数保持 `load_sources(paths)`。

## 支持格式

- 普通 `urlset` 与 `sitemapindex`，索引按相对/绝对 loc 递归展开，带环检测和深度上限（10 层）。
- gzip 压缩的 `.xml.gz`：按魔数或后缀识别，`gzip.GzipFile` + `iterparse` 分块流式解压解析，不整包读入。
- 来源支持本地路径、`file://`、`http(s)://` URL；单文件解析失败只记录样本并跳过，不影响其他来源。
- 兼容带/不带 XML 命名空间的站点地图。

## 处理流程

- 按 loc 去重（保留 lastmod/changefreq 更新的一条），按站点（域名+端口）分组。
- 优先级 = 变更频率分（always…never）与 lastmod 老化分（180 天半衰期），分数降序并写入 1 起的 `priority`；长期未变更逐步降权。
- robots：`build_robots` 支持传入规则文本/文件，也可 `auto_discover=True` 在线拉取；冲突链接默认排除并记录原因。
- 落盘：`refresh_all(entries, out_dir, robots_rules=...)` 按站点写 JSON，无变化不重写；新增 `new`、变更 `updated`、消失 `stale`、保留 `active`，排除项写入同文件 `excluded`。

## 落盘结构

```text
out_dir/
├── shop.example.json   # {site, refreshed_at, count, entries[], excluded[]}
└── news.example.json
```

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
