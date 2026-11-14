# contenthash

稳定正文指纹：剔除模板噪声后按段落分块，对稳定块集合取并集哈希；相似度按块重合率计算，超阈值判近重复。纯 Python 标准库，无第三方依赖。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 剔除规则

- 整块跳过：`script`、`style`、`nav`、`header`、`footer`、`aside`、`noscript`、`template` 标签及其全部子树。
- 广告块：任意标签的 `class`/`id` 含 `ad`、`ads`、`advert`、`banner`、`sponsor`、`promo`、`googleads` 等标识（支持 `ad-banner`、`ad_slot` 这类连字符/下划线写法）即整块剔除。
- 文本归一化：先做 Unicode NFKC（全半角统一），所有 Unicode 标点替换为空格，连续空白折叠为单空格并去首尾空白，因此标点和空白差异不再影响指纹。
- 分块：按 `p`、`div`、`section`、`article`、`li`、`td`、标题等块级元素切分，空块丢弃，重复段落去重，得到稳定块集合。

## 指纹与阈值口径

- `fingerprint(html)` 返回 `Fingerprint`：`hash`（稳定块哈希排序后取 SHA256 的并集指纹）、`raw_hash`（整页原始 SHA256，保留对原始页面的区分度）、`blocks`/`block_hashes`、`block_count`、`short`、`confidence`。
- `similar(a, b, threshold=0.8)` 返回 `Similarity`（布尔值即 `near_duplicate`）：`score` 为稳定块集合的 Jaccard 重合率 `交集/并集`，另含 `matched`、双方块数、实际生效阈值、`confidence` 和中文 `reason` 判定依据。
- 阈值口径：常规正文用调用方阈值（默认 `0.8`）；稳定块不超过 4 个视为短正文，自动降阈到 `0.6`（`SHORT_THRESHOLD`），减少短正文的误判。
- 证据门限：任一侧稳定块少于 2 个时即使 100% 重合也不判近重复，仅给低置信度参考；置信度按 `块数/4` 计算并封顶 1.0，块越少置信度越低。
- 阈值可调：`similar(a, b, threshold=...)` 或修改模块常量 `DEFAULT_THRESHOLD`/`SHORT_THRESHOLD`；短正文场景固定使用降后阈值，自定义阈值在非短正文（≥5 块）时生效。

## 接口示例

```python
from contenthash import fingerprint, similar

fp = fingerprint('<nav>x</nav><p>正文段落一</p><p>正文段落二</p>')
print(fp.hash, fp.block_count, fp.confidence)
result = similar(html_a, html_b, threshold=0.8)
print(result.score, bool(result), result.reason)
```
