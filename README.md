# crawlshard

分片双活：主机持租约写入，备机只读；租约到期后备机竞选，单调任期拒绝旧写。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 任期口径
- 首个 elect 的节点成主，term 从 0 开始，租约 `LEASE_SECONDS` 秒。
- 租约有效内他人 elect 只成为备机，term 不变。
- 租约到期后竞选成功 term +1，单调递增，旧主降级为 stale。
- 备机与旧任期写入一律拒绝，计入 rejected；未注册节点按客户端转发给主确认。

## 切换口径
- offset 是分片级已确认位点，备机 elect 即可见，新主从该位点续写。
- `expire_lease(shard)` 模拟主机失联；竞选需过半数，少数派留在备机。
- `set_partition(shard, groups)` 模拟脑裂：少数派拒绝写入并记录 events。
- elect/write 返回 term、role、rejected；`status(shard)` 看全貌。
