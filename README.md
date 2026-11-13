# tlssafe

现状：异常统一包成FetchError，任何错误都按同样次数重试。

问题：证书过期和网络错误混在一起，日志定位不到站点和证书，也没有放行流程。

目标：区分过期、主机名不匹配和链不完整，证书错误不重试，放行写审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
