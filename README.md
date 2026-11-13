# trafficmask

现状：请求按行原样落盘，cookie和手机号明文，回放原样读出。

问题：共享环境能看到凭据；回放头对不上签名失效；跨天文件串不起来。

目标：可脱敏录制与确定性回放，带批次号、跨文件检索和演练隔离。

\`\`\`bash
python3 demo.py
python3 -m unittest discover -s tests
\`\`\`

## 脱敏规则（record(..., mask=True)）

- Cookie 头按 \`name=value\` 逐项掩码；Authorization/X-Token 等 token 头掩码值部分。
- 手机号（11 位，1[3-9] 开头）在 url、body、其余头中统一掩码。
- 掩码保留长度与前后缀：cookie/token 留前 2 后 2，手机号留前 3 后 2，中段补 \`*\`。
- 值长度 <= 前缀+后缀（如 \`sid=abc\`）时整体打码为等长 \`*\`。
- 默认 \`mask=False\` 原样落盘（兼容既有断言）；脱敏为不可逆，回放得到的是掩码后的请求。

## 录制与回放格式

- 每行一条：\`{"batch": "...", "masked": bool, "request": {...}}\`，batch 默认 \`"default"\`，可跨天文件串联。
- \`replay(path)\` 按原 method/url/headers/body 重建请求列表；文件不存在返回 \`[]\`，行损坏抛带行号的 ValueError。
- 回放默认写入独立演练库 \`<文件名>.drill.jsonl\`（\`drill_path\` 改位置，\`drill_path=False\` 关闭），不污染录制文件。
- \`find(batch, paths)\` 跨文件按批次号检索，paths 支持文件、列表或目录；路径不存在抛 FileNotFoundError。

