# dupfinder

基于分片指纹的近重复检测，纯 Python 标准库，接口为 `index(docs)` / `query(doc)`。

**指纹口径**：正文小写归一后切 5 字符滑动分片（shingle），每片取 blake2b-64
哈希作为指纹，指纹倒排索引按阈值召回候选（召回阈值 = 判定阈值 × 0.5，过热
片段按文档频率上限跳过），查询为倒排查表而非两两比较，可支撑百万级语料。

**阈值口径**：候选用词集合（英文按词、中文按字二元组）复核。长度相近取
Jaccard；短文本词数 × 3 ≤ 长文本时判为长短悬殊，改用包含度（交集 / 较短者）
归一。相似度 ≥ `threshold`（默认 0.6）判重。`explain(doc)` 返回相似度、
共同片段和判定依据；查询 id 已在库中时排除自匹配。

索引可增量 `index()`，可 `save(path)` / `DupFinder.load(path)` 持久化。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
