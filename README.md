# crawlshard

现状：分片归属只记最后一个节点，任何节点都能写。

问题：切换出现双写，备机没有进度，脑裂无法发现。

目标：租约加单调任期，旧任期拒绝写入，切换从位点续写。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

**任期口径**：`configure` 配置成员后进入强制模式，首次竞选 term=1，此后每次成功竞选 term 单调 +1；租约有效时抢占被拒且任期不变，未配置分片保持 term=0 引导语义。写请求带 `term` 参数，低于当前任期按 `stale_term` 拒绝，非主机按 `not_leader` 拒绝。

**切换口径**：主机靠 `heartbeat` 续租，租约到期视为失联；只有多数派可见的节点能竞选当选。脑裂时少数派的竞选与写入均拒绝（`minority_partition`）并计入 `rejected_writes`。位点随每次成功写入推进，新主机从最后确认的 `position` 续写，回退位点按 `stale_position` 拒绝。`status` 输出任期、角色（leader/standby/unreachable）、租约有效性、位点与拒绝写次数。
