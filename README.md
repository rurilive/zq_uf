# crawlprobe

现状：预检直接返回可用，没有任何检查。

问题：DNS 不通也排队，证书和 robots 问题后知后觉，封禁整批白跑。

目标：DNS、证书、robots、首页和封禁预检，缓存和拦截。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
