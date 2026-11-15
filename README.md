# coldhot

现状：归档后在线查询看不到历史数据。

问题：历史一直在线成本高，归档要改 SQL，恢复靠人工。

目标：冷热分层，查询透明路由并合并，恢复按元数据回迁。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现（`coldhot/`，仅标准库）：

- `storage.py`：冷层抽象，读写删分片 + 故障注入（不可用/内容损坏）。
- `tier.py`：归档记录位置/范围/行数/校验和；查询按时间窗自动路由在线+冷层并合并，冷层故障降级告警不返空；恢复校验行数与 SHA-256，失败保留副本可重试，`confirm_cleanup` 人工确认后才清理；归档、恢复两本账独立，`list_archives` 查范围，`export_restores` 导出恢复记录。

