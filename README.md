# containerexit

容器逃逸检测小工具。按**配置组合**判定，不再把特权容器一律报成高危。

## 判定原则

- 任何单项配置都不直接构成高危；特权模式、能力集、挂载路径、宿主命名空间、
  内核参数（sysctl）与 seccomp/apparmor 一起看，**组合命中明确逃逸路径才报 high**。
- 无高危组合时按中危组合 / 低危残余因子回退：`high > medium > low > none`。
- 结论只由安全相关配置决定，同一配置（`config_hash`）在任何宿主上结论一致，
  解决“同镜像不同宿主结论不一致”。

### 高危组合（H01–H14，均为可走通的逃逸路径）

| 规则 | 组合 |
| --- | --- |
| H01 | 特权 + 共享宿主 PID/IPC 命名空间 |
| H02 | 特权 + 可写挂载宿主敏感目录（/、/etc、/var 等） |
| H03 | 非特权 CAP_SYS_ADMIN + 可写宿主敏感目录 |
| H04 | 特权 + 宿主块设备直通（/dev/sd*、/dev/nvme* 等） |
| H05 | CAP_SYS_RAWIO + /dev/mem、/dev/kcore 等字符设备 |
| H06 | 可写 docker/containerd socket + 特权或危险能力 |
| H07 | hostPID + CAP_SYS_PTRACE（ptrace 注入宿主进程） |
| H08/H12 | CAP_SYS_ADMIN / 特权 + 可写 /proc/sys |
| H09/H13 | CAP_SYS_ADMIN / 特权 + 可写 cgroup（release_agent） |
| H10 | CAP_SYS_MODULE + 内核模块目录 |
| H11 | `kernel.core_pattern` 管道 + CAP_SYS_ADMIN |
| H14 | 危险 sysctl 放开 + CAP_SYS_ADMIN |

中危为单项强原语（单独特权、单独 SYS_ADMIN、单独 socket、hostPID、
hostNetwork+NET_ADMIN、单独可写宿主目录等）；只读挂载、unconfined
seccomp/apparmor、只读 socket 等记为低危因子。

## 入口

### `check(container) -> dict`

入参字段（均可缺省，除 `name`）：`name`、`host`、`image`、`privileged`、
`caps`（支持 `SYS_ADMIN`/`CAP_SYS_ADMIN`）、`mounts`（字符串
`宿主:容器[:ro]` 或 `{source,target,mode}` 字典）、`host_pid`、`host_ipc`、
`host_net`、`host_uts`、`sysctls`、`seccomp`、`apparmor`。

返回：

- `level`：`high` / `medium` / `low` / `none`
- `combos`：命中的组合，每条含规则 ID、说明、等级和相关配置的**实际取值**
- `basis`：完整判定依据，每项配置都带实际取值，可逐项展开核对
- `advice`：处置建议按 `online`（不停机可做）/ `offline`（需重建容器）分开
- `config_hash`：配置指纹，跨宿主对比用

### `report() -> dict`

- `host_groups`：同一主机的多个容器合并展示（含各等级计数），`containers`
  里保留单容器完整结论，可逐容器展开；
- `config_groups`：同一配置在不同宿主上的结论放在一起，`consistent` 标明
  跨宿主结论是否一致；
- `marked` / 计数：误报标记区。

### `mark_false_positive(host, container, reason="", actual_config=None)`

安全同学对误报做标记，可带上实际配置；不传则自动留存该容器的 `basis`
快照。标记不改判定结论，只从活跃风险计数中剔除并保留复核依据。

### `reset()`

清空容器记录与误报标记。

## 演示与测试

```bash
python3 demo.py
python3 -m unittest discover -s tests -v
```
