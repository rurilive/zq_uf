# pcapflow

安全方向的流量会话分析工具：把抓包里的包按**五元组归并成会话**、做协议解析，
再用**带版本的规则集**对会话出横向扫描 / 外联 / 隧道 / 异常端口四类结论。
规则变更后只重跑受影响规则涉及的会话，误报可以标记并回灌成回归用例。

## 解决的问题

旧实现逐包看目的端口，导致：同一会话重复计数、备份任务（SMB 445）被误报成
异常、真正的外联淹没在几万条会话里、规则改动要重跑整包且无法验证改对没有。
现在的处理方式：

- **会话归并**：规范化五元组（两端点排序），正反方向的包归到同一条会话，
  方向按内外网关系分为 lateral / outbound / inbound / external；
- **协议解析**：端口映射 + 显式提示 + 载荷嗅探三级识别，内置
  HTTP/TLS/DNS/SSH/SMB 解析器，支持注册自定义协议；
- **会话级判定**：规则对会话出结论并附证据包序号，证据可直接 `get_packet` 回放；
- **规则版本**：每条规则带 id/version/配置指纹，报告写入版本号，
  `compare()` 对比同批包跨版本的命中差异；
- **增量重跑**：按规则指纹复用未变更规则的结论，`incremental` 字段给出
  复用/重跑明细；抓包内容变化（哈希不一致）才重建会话；
- **误报回灌**：`mark_false_positive` 生成用例，后续命中自动抑制，
  `run_cases` 跑回归并在规则再次告警时报 regressed；
- **可观测**：协议解析失败的包、未覆盖协议的会话分别单独列出，
  `query()` 支持按主机、时间窗、方向、判定类型下钻。

## 快速开始

```python
from pcapflow import analyze, load, report, query, compare

load("cap1", packets)                       # packets: 包字典列表
out = analyze("cap1", rule_version="1.1.0")
out["sessions"]        # 会话总数（五元组归并后）
out["hit_sessions"]    # 命中会话数，同会话不重复计数
out["hits"]            # 命中：类型、规则 id/版本、原因、evidence 证据包序号
out["rule_version"]    # 规则版本写进结果
out["failed"]          # 协议解析失败的包（含原因）
out["uncovered"]       # 未覆盖协议的会话
out["incremental"]     # 本次复用/重跑了哪些规则

report("cap1")                            # 按主机/类型汇总，含规则版本
query(host="10.0.0.5", type="lateral_scan")   # 告警会话下钻
compare("cap1", "1.0.0", "1.1.0")         # 跨版本命中差异
```

完整端到端示例见 `demo.py`：`python3 -I demo.py`。

## 包记录格式

```python
{"no": 1, "src": "10.0.0.5", "dst": "10.0.0.9",
 "sport": 49001, "dport": 445, "proto": "tcp", "ts": 1.0,
 "app": "tls", "payload": b"...", "dns_query": "example.com"}
```

`no/src/dst/dport` 必填；`sport/proto/ts/size` 可选；`app` 显式提示协议，
`payload` 为字节串（或 `hex:` 开头的十六进制字符串），无载荷时按端口识别
不算解析失败。

## 规则版本

- `1.0.0`：横向扫描阈值 5 台内网主机，DNS 隧道查询长度阈值 60；
- `1.1.0`（默认）：阈值分别收紧到 3 和 40；
- 自定义：`build_ruleset("1.1.0", overrides={...})` 后 `register_ruleset`，
  `analyze(..., ruleset_name=...)` 引用。

备份服务器目标（默认 `10.0.0.9`）和备份端口（445/139）在规则配置里白名单化，
备份任务不再产生横向扫描或异常端口误报。

## 模块结构

| 模块 | 职责 |
| --- | --- |
| `netutils.py` | 内外网判定、五元组规范化、会话 ID |
| `session.py` | 五元组归并、方向识别、证据包归集 |
| `protocols.py` | 协议注册表、内置解析器、失败/未覆盖归集 |
| `rules.py` | 版本化规则集与四类判定规则 |
| `engine.py` | `load`/`analyze`、会话缓存、规则级增量、`compare`/`query` |
| `feedback.py` | 误报标记、用例导入导出、回归验证 |
| `report.py` | 按主机/类型汇总报告 |

## 测试

```bash
python3 -I -m unittest discover -s tests
```

38 个用例覆盖会话归并（含反向包）、四类判定、协议解析失败与未覆盖、
规则版本与指纹增量、跨版本对比、误报回灌与回归、报告下钻。
