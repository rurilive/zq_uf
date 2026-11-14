# crawlshard

现状：分片归属只记最后一个节点，任何节点都能写。

问题：切换出现双写，备机没有进度，脑裂无法发现。

目标：租约加单调任期，旧任期拒绝写入，切换从位点续写。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

口径：同一分片同一时刻只有一个持租约的主机，租约 5 秒，写成功即续租。
竞选：租约到期或主机失联（_down）后备机 elect 竞选，任期号单调递增，多数派可达才接任。
写入：只有当前任期的主机能确认写入；旧任期主机按 stale_term 拒绝；脑裂少数派按 minority 拒绝，均计入 rejected。
位点：position 是主机最后确认的写入序号，切换后新主从该位点续写，不重复抓。

返回：elect/write 都带 term（任期）、role（primary/standby/stale）、position（位点）、rejected（拒绝写次数）。
备机只读：备机不持租约、不落日志，写请求转发主机确认（返回 via 字段）。
故障注入：shard._down 模拟失联（退出多数派），shard._isolated 模拟分区（仍占多数派但不可达）。
demo.py 模拟主机失联：备机竞选接任并从位点续写，旧主恢复后写入被拒。
