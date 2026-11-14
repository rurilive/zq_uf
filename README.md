# urlcanon

采集链接的等价类模块：规则管线生成规范键，并查集维护等价类，索引可持久化。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

接口：normalize(url) 返回规范键；same(a,b) 判等价；merge(a,b) 合并两类并返回类代表。
辅助输出：representative(url) 取类代表，rule_hits(url) 看规则命中，merge_log() 看合并记录。
索引默认落盘 urlcanon-index.json，可用环境变量 URLCANON_INDEX 或 canon.configure(path) 改路径。
非法输入（缺 scheme、不支持的 scheme、非法端口、非法百分号编码、损坏的索引文件）抛 UrlCanonError。

规则优先级（高先应用；冲突以高优先级为准，低优先级不得复活高优先级删掉的内容）：
trim 110 > case 100 > default-port 90 > percent-encoding 80 > default-index 70
> dot-segments 60 > fragment 50 > tracking-params 40 > query-sort 30。
仅默认端口（http:80/https:443）被移除，非默认端口仍区分页面。
并查集口径：规范键相同的链接同处一类；类代表 = 类中最早注册的键，
注册序号随索引落盘，合并与重启都不漂移；每次合并追加一条持久化记录。

