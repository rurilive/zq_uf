# pagecore

现状：去掉标签后整页返回，导航和推荐位都算正文。

问题：字数虚高，短正文被丢，规则写死改版就失效。

目标：按块打分抽正文，短正文保留并给置信度，规则按站点覆盖。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

`extract(html)` 返回正文、把握程度和逐块判定；`blocks(html)` 列出所有块及剔除原因。
正文按内容密度和链接比例打分，nav/comment/recommend 等噪声块硬剔除，短正文救回但把握封顶。
`configure(site, **规则)` 按站点覆盖阈值，改版只改配置。
`extract(..., url=...)` 失败时回退到该地址上一次结果并标 `fallback`；`diff(a, b)` 对比两次正文。
