# snapdiff

页面快照与变更检测：剔除噪声后按结构块比对，变更定位到块，快照按版本保留。

## 快照口径

- `snapshot(page, store=None)`，`page` 含 `html`/`site`/`url`/`ts`。
- `hash` 是原始页哈希（兼容旧口径，任何字节变化都会变）；比对不看它。
- `blocks` 是结构块指纹：先剔除 `script/style/noscript/template/iframe/ins`
  及 class/id 命中广告、计数器特征的子树，再对最外层块级标签的归一化文本取指纹。
- 传 `store` 时快照 zlib 压缩落盘，版本号按站点递增，可按版本或时间取回。

## 比对口径

- `diff(old, new, store=None)` 先按指纹配对未变块（吸收位置移动），再按块 id
  配对修改块，其余记新增/删除；返回 `{changed, blocks, summary, version}`。
- 每个变更块带 id、类型和前后摘要及块级样本（截断 200 字），供运营复核。
- `mark_false_positive(前指纹, 后指纹)` 标记误报样本，之后自动抑制；
  经 `store.mark_false_positive` 标记的样本按站点持久化，重开生效。
- 传 `store` 时比对结果落库，`store.query_changes(site, start, end)` 按站点+时间查。
- `store.cleanup()` 按保留期清理过期快照与变更记录，每站点至少留最新一版作基线。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
