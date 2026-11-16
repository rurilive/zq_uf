"""逃逸判定规则。

核心原则（对应需求）：
- 任何单项配置都不直接构成高危；只有配置组合命中明确的逃逸路径才报 high；
- 没有高危组合时，按中危组合 / 低危因子回退分级（medium / low / none）；
- 每条命中规则给出涉及配置的实际取值，处置建议按“不停机可做（online）”
  和“需重建/重启容器（offline）”分开。

规则依据的是常见 Linux 容器逃逸路径：宿主文件系统 bind 可写、
宿主命名空间共享、危险能力、容器引擎 socket、内核参数篡改等。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import DANGEROUS_CAPS, Facts

DANGEROUS_SYSCTLS = (
    "kernel.unprivileged_userns_clone",
    "user.max_user_namespaces",
    "kernel.kptr_restrict",
    "kernel.yama.ptrace_scope",
    "kernel.perf_event_paranoid",
)


def _entry(key, value):
    return {"key": key, "value": value}


@dataclass
class Rule:
    id: str
    title: str
    level: str
    # 返回 None 表示未命中；返回命中配置的实际取值列表。
    basis_fn: object
    advice: dict = field(default_factory=lambda: {"online": [], "offline": []})
    # fallback 规则只在没有高危命中时参与评估。
    fallback: bool = False


# ---- 高危组合：每一条对应一条可走通的逃逸路径 ---------------------------

def _h01(facts: Facts):
    if facts.privileged and (facts.host_pid or facts.host_ipc):
        out = [_entry("privileged", True)]
        if facts.host_pid:
            out.append(_entry("host_pid", True))
        if facts.host_ipc:
            out.append(_entry("host_ipc", True))
        return out
    return None


def _h02(facts: Facts):
    mounts = facts.mounts_of("sensitive") if facts.privileged else []
    if mounts:
        return [_entry("privileged", True), _entry("mounts", mounts)]
    return None


def _h03(facts: Facts):
    if not facts.privileged and facts.has_cap("SYS_ADMIN"):
        mounts = facts.mounts_of("sensitive")
        if mounts:
            return [
                _entry("privileged", False),
                _entry("caps", list(facts.caps)),
                _entry("mounts", mounts),
            ]
    return None


def _h04(facts: Facts):
    if facts.privileged:
        mounts = facts.mounts_of("device")
        if mounts:
            return [_entry("privileged", True), _entry("mounts", mounts)]
    return None


def _h05(facts: Facts):
    if facts.has_cap("SYS_RAWIO"):
        mounts = facts.mounts_of("special_device", writable_only=False)
        if mounts:
            return [_entry("caps", list(facts.caps) if not facts.privileged else "ALL (privileged)"),
                    _entry("mounts", mounts)]
    return None


def _h06(facts: Facts):
    mounts = facts.mounts_of("socket")
    if mounts and (facts.privileged or facts.dangerous_caps):
        return [
            _entry("privileged", facts.privileged),
            _entry("caps", list(facts.caps)),
            _entry("mounts", mounts),
        ]
    return None


def _h07(facts: Facts):
    if not facts.privileged and facts.host_pid and facts.has_cap("SYS_PTRACE"):
        return [
            _entry("host_pid", True),
            _entry("caps", list(facts.caps)),
        ]
    return None


def _h08(facts: Facts):
    if not facts.privileged and facts.has_cap("SYS_ADMIN"):
        mounts = facts.mounts_of("proc_sys")
        if mounts:
            return [_entry("caps", list(facts.caps)), _entry("mounts", mounts)]
    return None


def _h09(facts: Facts):
    if not facts.privileged and facts.has_cap("SYS_ADMIN"):
        mounts = facts.mounts_of("cgroup")
        if mounts:
            return [_entry("caps", list(facts.caps)), _entry("mounts", mounts)]
    return None


def _h10(facts: Facts):
    if facts.has_cap("SYS_MODULE"):
        mounts = facts.mounts_of("modules", writable_only=False)
        if mounts:
            return [
                _entry("caps", list(facts.caps) if not facts.privileged else "ALL (privileged)"),
                _entry("mounts", mounts),
            ]
    return None


def _h11(facts: Facts):
    if facts.core_pattern_pipe and facts.has_cap("SYS_ADMIN"):
        return [
            _entry("sysctls", {"kernel.core_pattern": facts.sysctls["kernel.core_pattern"]}),
            _entry("caps", list(facts.caps) if not facts.privileged else "ALL (privileged)"),
        ]
    return None


def _h12(facts: Facts):
    if facts.privileged:
        mounts = facts.mounts_of("proc_sys")
        if mounts:
            return [_entry("privileged", True), _entry("mounts", mounts)]
    return None


def _h13(facts: Facts):
    if facts.privileged:
        mounts = facts.mounts_of("cgroup")
        if mounts:
            return [_entry("privileged", True), _entry("mounts", mounts)]
    return None


def _h14(facts: Facts):
    hit = {k: v for k, v in facts.sysctls.items() if k in DANGEROUS_SYSCTLS}
    if hit and facts.has_cap("SYS_ADMIN"):
        return [
            _entry("sysctls", hit),
            _entry("caps", list(facts.caps) if not facts.privileged else "ALL (privileged)"),
        ]
    return None


HIGH_RULES = [
    Rule("H01", "特权容器共享宿主 PID/IPC 命名空间，可直接操作宿主进程",
         "high", _h01, {
             "online": ["在宿主上对关键宿主进程加保护（systemd ProtectSystem/NoNewPrivileges），临时收紧 /proc 访问"],
             "offline": ["去掉 --privileged，仅保留业务必需能力；关闭 hostPID/hostIPC，改用独立命名空间后重建容器"],
         }),
    Rule("H02", "特权容器可写挂载宿主敏感目录，可改写宿主文件实现逃逸",
         "high", _h02, {
             "online": ["将该 bind 挂载重新以只读方式挂载（mount -o remount,ro），临时阻断写入"],
             "offline": ["去掉 --privileged；移除宿主敏感目录挂载或改为只读/限定子目录，重建容器"],
         }),
    Rule("H03", "CAP_SYS_ADMIN 叠加可写宿主目录，可重新挂载并篡改宿主文件系统",
         "high", _h03, {
             "online": ["将相关 bind 挂载 remount 为只读，临时关闭写入面"],
             "offline": ["在 securityContext 中 drop CAP_SYS_ADMIN；挂载改为只读或缩小到业务子目录后重建"],
         }),
    Rule("H04", "特权容器直通宿主块设备，可绕过文件系统直接读写宿主磁盘",
         "high", _h04, {
             "online": ["在宿主上对该设备应用 udev/ACL 限制或临时取消容器内设备节点访问"],
             "offline": ["移除 --devices 块设备直通与 --privileged，确需存储改用普通卷，重建容器"],
         }),
    Rule("H05", "CAP_SYS_RAWIO 叠加宿主内存类字符设备，可直接访问宿主物理内存",
         "high", _h05, {
             "online": ["在宿主上临时收紧 /dev/mem 等设备权限（内核 CONFIG_STRICT_DEVMENU 限制之外补充 udev 规则）"],
             "offline": ["drop CAP_SYS_RAWIO 并移除 /dev/mem、/dev/kcore 等设备映射后重建容器"],
         }),
    Rule("H06", "可写容器引擎 socket 叠加特权/危险能力，可经 API 创建特权容器接管宿主",
         "high", _h06, {
             "online": ["在宿主上临时收紧 socket 文件权限并审计容器引擎 API 调用，确认无越权调用"],
             "offline": ["移除 docker.sock/containerd.sock 挂载，改用受控管理代理（带鉴权与最小权限）后重建"],
         }),
    Rule("H07", "共享宿主 PID 且具备 CAP_SYS_PTRACE，可 ptrace 注入宿主进程",
         "high", _h07, {
             "online": ["宿主侧设置 kernel.yama.ptrace_scope=2/3 限制 ptrace，并审计 /proc 访问"],
             "offline": ["关闭 hostPID 并 drop CAP_SYS_PTRACE，重建容器"],
         }),
    Rule("H08", "CAP_SYS_ADMIN 叠加可写 /proc/sys，可篡改宿主内核参数",
         "high", _h08, {
             "online": ["将 /proc/sys 挂载 remount 为只读，宿主侧核对关键 sysctl 当前值"],
             "offline": ["drop CAP_SYS_ADMIN，移除 /proc/sys 可写挂载，需要的内核参数在宿主统一管理后重建"],
         }),
    Rule("H09", "CAP_SYS_ADMIN 叠加可写 cgroup，可借 release_agent 执行宿主程序",
         "high", _h09, {
             "online": ["将 cgroup 挂载 remount 为只读，临时关闭 release_agent 写入面"],
             "offline": ["drop CAP_SYS_ADMIN，移除 /sys/fs/cgroup 可写挂载后重建容器"],
         }),
    Rule("H10", "CAP_SYS_MODULE 叠加内核模块目录，可加载恶意内核模块",
         "high", _h10, {
             "online": ["宿主侧临时启用模块签名校验（module.sig_enforce=1）并审计已加载模块"],
             "offline": ["drop CAP_SYS_MODULE，移除 /lib/modules 挂载后重建容器"],
         }),
    Rule("H11", "core_pattern 管道配合 CAP_SYS_ADMIN，可在宿主崩溃路径上执行指定程序",
         "high", _h11, {
             "online": ["宿主侧立即重置 kernel.core_pattern 为安全的本地路径，并审计触发记录"],
             "offline": ["移除容器对该 sysctl 的写权限与 CAP_SYS_ADMIN，重建容器"],
         }),
    Rule("H12", "特权容器可写 /proc/sys，可直接修改宿主内核参数",
         "high", _h12, {
             "online": ["将 /proc/sys 挂载 remount 为只读并核对宿主 sysctl"],
             "offline": ["去掉 --privileged，移除 /proc/sys 可写挂载后重建容器"],
         }),
    Rule("H13", "特权容器可写 cgroup，可借 release_agent 在宿主执行命令",
         "high", _h13, {
             "online": ["将 cgroup 挂载 remount 为只读，临时关闭 release_agent 写入面"],
             "offline": ["去掉 --privileged，移除 /sys/fs/cgroup 可写挂载后重建容器"],
         }),
    Rule("H14", "危险内核参数放开叠加 CAP_SYS_ADMIN，扩大命名空间/ptrace 等逃逸面",
         "high", _h14, {
             "online": ["宿主侧立即回收相关 sysctl 到安全基线值并审计"],
             "offline": ["drop CAP_SYS_ADMIN，sysctl 由宿主统一按基线管理，重建容器"],
         }),
]


# ---- 中危组合 / 因子（无高危命中时评估） ---------------------------------

def _m01(facts: Facts):
    mounts = facts.mounts_of("socket")
    if mounts and not facts.privileged and not facts.dangerous_caps:
        return [_entry("mounts", mounts)]
    return None


def _m02(facts: Facts):
    if facts.privileged:
        return [_entry("privileged", True)]
    return None


def _m03(facts: Facts):
    if not facts.privileged and "SYS_ADMIN" in facts.caps:
        return [_entry("caps", list(facts.caps))]
    return None


def _m04(facts: Facts):
    if not facts.privileged and facts.dangerous_caps:
        return [_entry("caps", list(facts.caps))]
    return None


def _m05(facts: Facts):
    if facts.host_pid:
        out = [_entry("host_pid", True)]
        if facts.host_ipc:
            out.append(_entry("host_ipc", True))
        return out
    return None


def _m06(facts: Facts):
    if facts.host_net and facts.has_cap("NET_ADMIN"):
        return [_entry("host_net", True),
                _entry("caps", list(facts.caps) if not facts.privileged else "ALL (privileged)")]
    return None


def _m07(facts: Facts):
    mounts = facts.mounts_of("sensitive")
    if mounts and not facts.privileged and "SYS_ADMIN" not in facts.caps:
        return [_entry("mounts", mounts)]
    return None


def _m08(facts: Facts):
    mounts = facts.mounts_of("socket", writable_only=False)
    ro = [m for m in mounts if not m["rw"]]
    if ro:
        return [_entry("mounts", ro)]
    return None


MEDIUM_RULES = [
    Rule("M01", "单独可写容器引擎 socket：API 本身具备拉起特权容器的能力",
         "medium", _m01, {
             "online": ["收紧 socket 文件权限，审计容器引擎 API 调用"],
             "offline": ["移除 socket 挂载，改用带鉴权的管理代理后重建容器"],
         }),
    Rule("M02", "单独特权模式：能力面全开，但当前配置未出现可直接走通的逃逸路径",
         "medium", _m02, {
             "online": ["宿主侧用 seccomp/AppArmor 临时加约束并监控容器系统调用"],
             "offline": ["去掉 --privileged，按业务实际需求逐项添加 capability 后重建容器"],
         }, fallback=True),
    Rule("M03", "单独 CAP_SYS_ADMIN：等价于大量挂载/命名空间管理权限",
         "medium", _m03, {
             "online": ["宿主侧临时追加 AppArmor/seccomp 约束限制 mount/namespace 调用"],
             "offline": ["drop CAP_SYS_ADMIN，按最小权限拆分能力后重建容器"],
         }, fallback=True),
    Rule("M04", "持有危险能力集合",
         "medium", _m04, {
             "online": ["宿主侧审计相关能力的实际调用情况"],
             "offline": ["按业务最小权限 drop 非必需的危险能力后重建容器"],
         }, fallback=True),
    Rule("M05", "单独共享宿主 PID/IPC 命名空间：可观测宿主进程与 IPC 对象",
         "medium", _m05, {
             "online": ["宿主侧收紧 ptrace 范围（kernel.yama.ptrace_scope）并审计 /proc 访问"],
             "offline": ["关闭 hostPID/hostIPC，使用独立命名空间后重建容器"],
         }, fallback=True),
    Rule("M06", "共享宿主网络命名空间且具备 CAP_NET_ADMIN，可操纵宿主网络栈",
         "medium", _m06, {
             "online": ["宿主侧审计 iptables/路由/网卡变更，临时收紧网络相关接口"],
             "offline": ["关闭 hostNetwork 或 drop CAP_NET_ADMIN，重建容器"],
         }),
    Rule("M07", "单独可写挂载宿主敏感目录：可直接改写宿主文件",
         "medium", _m07, {
             "online": ["将相关 bind 挂载 remount 为只读"],
             "offline": ["移除该挂载或缩小到业务必需的子目录、改为只读后重建容器"],
         }),
    Rule("M08", "只读挂载容器引擎 socket：信息泄露面仍在，写入被阻断",
         "medium", _m08, {
             "online": ["审计对 socket 的只读访问来源"],
             "offline": ["如无业务必要直接移除该挂载，重建容器"],
         }),
]


# ---- 低危因子：无高/中危命中时的残余风险与建议 ---------------------------

@dataclass
class Factor:
    key: str
    level: str
    detect: object
    advice: dict


def _detect_dangerous_sysctls(facts: Facts):
    hit = {k: v for k, v in facts.sysctls.items() if k in DANGEROUS_SYSCTLS}
    return hit or None


FACTORS = [
    Factor("dangerous_sysctls", "low", _detect_dangerous_sysctls, {
        "online": ["宿主侧将相关 sysctl 恢复到安全基线并纳入配置巡检"],
        "offline": ["在宿主镜像/启动模板中固化安全基线，禁止容器自行覆盖"],
    }),
    Factor("seccomp", "low",
           lambda f: "unconfined" if f.seccomp == "unconfined" else None, {
               "online": ["记录并审计容器系统调用，评估临时 AppArmor 约束"],
               "offline": ["恢复默认 seccomp profile 或使用裁剪后的自定义 profile，重建容器"],
           }),
    Factor("apparmor", "low",
           lambda f: "unconfined" if f.apparmor == "unconfined" else None, {
           "online": ["宿主侧临时下发受限 AppArmor 配置文件"],
           "offline": ["为容器配置默认/自定义 AppArmor profile，重建容器"],
       }),
    Factor("host_ipc", "low", lambda f: True if f.host_ipc else None, {
        "online": ["审计容器对宿主 IPC 对象（共享内存/信号量）的访问"],
        "offline": ["无共享 IPC 需求时关闭 hostIPC，重建容器"],
    }),
    Factor("host_net", "low", lambda f: True if f.host_net else None, {
        "online": ["审计容器内网络配置变更与抓包行为"],
        "offline": ["无共享网络需求时关闭 hostNetwork，重建容器"],
    }),
    Factor("host_uts", "low", lambda f: True if f.host_uts else None, {
        "online": ["审计 hostname/内核参数相关操作"],
        "offline": ["关闭 hostUTS，重建容器"],
    }),
    Factor("sensitive", "low",
           lambda f: f.mounts_of("sensitive", writable_only=False) or None, {
               "online": ["宿主侧审计容器对挂载目录的读取内容，确认无敏感信息泄露"],
               "offline": ["缩小挂载范围到业务必需子目录或移除挂载，重建容器"],
           }),
    Factor("device", "low",
           lambda f: f.mounts_of("device", writable_only=False) or None, {
               "online": ["宿主侧审计该块设备的访问与 I/O"],
               "offline": ["移除块设备直通，改用普通卷后重建容器"],
           }),
    Factor("special_device", "low",
           lambda f: f.mounts_of("special_device", writable_only=False) or None, {
               "online": ["宿主侧收紧对应字符设备权限并审计访问"],
               "offline": ["移除特殊设备映射，重建容器"],
           }),
    Factor("proc_sys", "low",
           lambda f: f.mounts_of("proc_sys", writable_only=False) or None, {
               "online": ["将 /proc/sys 挂载 remount 为只读"],
               "offline": ["移除 /proc/sys 挂载，sysctl 由宿主统一管理后重建"],
           }),
    Factor("cgroup", "low",
           lambda f: f.mounts_of("cgroup", writable_only=False) or None, {
               "online": ["将 cgroup 挂载 remount 为只读"],
               "offline": ["移除 /sys/fs/cgroup 挂载后重建容器"],
           }),
    Factor("modules", "low",
           lambda f: f.mounts_of("modules", writable_only=False) or None, {
           "online": ["宿主侧开启模块签名校验并审计模块加载"],
           "offline": ["移除 /lib/modules 挂载，重建容器"],
       }),
]


def _dedupe(items):
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def evaluate(facts: Facts):
    """返回 (level, combos, advice)。combos 为全部命中的规则（含实际取值）。"""
    combos = []
    for rule in HIGH_RULES:
        basis = rule.basis_fn(facts)
        if basis is not None:
            combos.append({
                "id": rule.id,
                "title": rule.title,
                "level": rule.level,
                "basis": basis,
            })

    if not combos:
        for rule in MEDIUM_RULES:
            basis = rule.basis_fn(facts)
            if basis is not None:
                combos.append({
                    "id": rule.id,
                    "title": rule.title,
                    "level": rule.level,
                    "basis": basis,
                })

    if combos:
        level = "high" if any(c["level"] == "high" for c in combos) else "medium"
    else:
        level = "none"

    # 处置建议：命中规则 + 残余因子，按在线/离线分别聚合去重。
    online, offline = [], []
    matched_rules = [r for r in HIGH_RULES + MEDIUM_RULES
                     if any(c["id"] == r.id for c in combos)]
    for rule in matched_rules:
        online.extend(rule.advice["online"])
        offline.extend(rule.advice["offline"])

    factor_hits = []
    if level == "none":
        for factor in FACTORS:
            value = factor.detect(facts)
            if value is not None:
                factor_hits.append({"key": factor.key, "value": value})
                online.extend(factor.advice["online"])
                offline.extend(factor.advice["offline"])
        level = "low" if factor_hits else "none"
    else:
        for factor in FACTORS:
            if factor.detect(facts) is not None:
                factor_hits.append({"key": factor.key,
                                    "value": _factor_value(facts, factor)})

    advice = {
        "online": _dedupe(online),
        "offline": _dedupe(offline),
        "none": [] if (online or offline) else ["当前配置未发现逃逸风险，保持基线巡检即可"],
    }
    return level, combos, factor_hits, advice


def _factor_value(facts: Facts, factor: Factor):
    scalar = {
        "seccomp": facts.seccomp,
        "apparmor": facts.apparmor,
        "host_net": facts.host_net,
        "host_ipc": facts.host_ipc,
        "host_uts": facts.host_uts,
    }
    if factor.key == "dangerous_sysctls":
        return {k: v for k, v in facts.sysctls.items() if k in DANGEROUS_SYSCTLS}
    if factor.key in scalar:
        return scalar[factor.key]
    return facts.mounts_of(factor.key, writable_only=False)
