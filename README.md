# condfetch

现状：普通GET封装，每次整页下载，请求头原样透传但不参与缓存。

问题：服务端支持ETag和Last-Modified却用不上，304被当失败，缓存键只看URL。

目标：条件请求加可共享缓存，支持304、键区分、容量淘汰和后台刷新。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
