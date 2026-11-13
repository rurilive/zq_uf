# linkfollow

现状：正则提取页面里的 href，返回全部链接。

问题：同页不同参数反复入队，外站混入，深度限制不生效。

目标：规范化、同站过滤、翻页归并、深度与数量上限。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
