# tokenforge

现状：支持 HS256 和 RS256 的 JWT 校验，tests 锁着正常签发和过期。

问题：alg none、kid 路径、过期边界、nbf、密钥轮换和签名比较都有缺口。

目标：收紧整条校验链，区分失败原因，支持宽限期轮换。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
