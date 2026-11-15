# crawlprobe2

现状：站点逐个串行探测，结果不缓存。

问题：几百个站点启动很慢，重复探测，失败不分临时和永久。

目标：并发加缓存，临时失败可重试，永久失败直接拦截。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
