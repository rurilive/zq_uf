# tokenforge

现状：支持 HS256 和 RS256 的 JWT 校验，tests 锁着正常签发和过期。

问题：alg none、kid 路径、过期边界、nbf、密钥轮换和签名比较都有缺口。

目标：收紧整条校验链，区分失败原因，支持宽限期轮换。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现要点：
- `verify_token(token, keys, now=None, *, allowed_algs=None, leeway=30)`，返回 payload dict。
- `allowed_algs` 显式开启算法（默认 HS256+RS256），`none` 一律拒绝。
- `keys` 只支持 dict：kid → 单个 key 或 key 列表（轮换宽限期，逐一尝试）。
- exp/nbf 按浮点比较；`leeway` 为 nbf 的时钟偏移宽限，默认 30 秒。
- 签名用 `hmac.compare_digest` 常量时间比较。
- 输入收紧：三段式、严格 base64url、header/payload 必须是 JSON 对象、拒绝重复声明、kid 非空且 ≤128 字符。
- `TokenError.reason` 区分 `alg` / `kid` / `signature` / `expired` / `not_yet` / `format` / `keys`。
