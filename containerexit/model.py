"""容器配置归一化。

把外部传入的容器描述统一成内部事实模型（Facts），同时产出：
- basis：判定依据，每项配置都带实际取值；
- config_hash：只由安全相关配置决定的指纹，同一配置在任何宿主上指纹相同，
  从而保证“同镜像/同配置在不同宿主上结论一致”，并支持跨宿主对比。
"""
from __future__ import annotations

import hashlib
import json

# ---- 敏感路径与设备分类 -------------------------------------------------

# 宿主敏感目录：bind 挂载且可写时，容器内可直接改写宿主文件。
SENSITIVE_PATHS = (
    "/",
    "/etc",
    "/root",
    "/home",
    "/var",
    "/var/lib",
    "/var/lib/docker",
    "/var/lib/containerd",
    "/var/log",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/boot",
    "/mnt",
    "/media",
    "/run",
)

# 宿主块设备前缀：直通后可绕过文件系统层读写宿主磁盘。
DEVICE_PREFIXES = (
    "/dev/sd",
    "/dev/xvd",
    "/dev/nvme",
    "/dev/vd",
    "/dev/hd",
    "/dev/mapper/",
    "/dev/disk/",
)

# 特殊字符设备：配合 CAP_SYS_RAWIO 可直接访问宿主物理内存/内核日志。
SPECIAL_DEVICES = {
    "/dev/mem",
    "/dev/kmem",
    "/dev/kcore",
    "/dev/port",
    "/dev/kmsg",
}

# 容器管理 socket：挂载即可通过 API 控制宿主上的容器引擎。
SOCKET_PATHS = {
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/var/run/containerd/containerd.sock",
    "/run/containerd/containerd.sock",
}

# 参与逃逸判定的危险能力（privileged 等价于拥有全部能力）。
DANGEROUS_CAPS = (
    "SYS_ADMIN",
    "SYS_MODULE",
    "SYS_PTRACE",
    "SYS_RAWIO",
    "DAC_READ_SEARCH",
    "DAC_OVERRIDE",
    "SYS_BOOT",
    "SYSLOG",
    "NET_ADMIN",
    "BPF",
    "PERFMON",
)

_MOUNT_SRC_KEYS = ("source", "src", "host_path")
_MOUNT_TGT_KEYS = ("target", "dst", "destination", "container_path")
_MOUNT_RO_KEYS = ("ro", "read_only", "readonly")


def _norm_path(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("mount path is required")
    path = value.strip()
    if len(path) > 1:
        path = path.rstrip("/")
    return path or "/"


def _norm_mount(item):
    """支持 "宿主:容器[:ro]" 字符串或 {source,target,mode} 字典两种写法。"""
    if isinstance(item, str):
        parts = item.split(":")
        if len(parts) == 1:
            source = target = _norm_path(parts[0])
            readonly = False
        elif len(parts) == 2:
            source, target = _norm_path(parts[0]), _norm_path(parts[1])
            readonly = False
        elif len(parts) == 3:
            source, target = _norm_path(parts[0]), _norm_path(parts[1])
            readonly = "ro" in parts[2]
        else:
            raise ValueError(f"invalid mount: {item!r}")
    elif isinstance(item, dict):
        source = next((item[k] for k in _MOUNT_SRC_KEYS if k in item), None)
        if not source:
            raise ValueError(f"invalid mount: {item!r}")
        source = _norm_path(source)
        target = next((item[k] for k in _MOUNT_TGT_KEYS if k in item), None)
        target = _norm_path(target or source)
        mode = str(item.get("mode", item.get("access", "rw")))
        readonly = "ro" in mode or any(bool(item.get(k)) for k in _MOUNT_RO_KEYS)
    else:
        raise ValueError(f"invalid mount: {item!r}")
    return {"source": source, "target": target, "rw": not readonly}


def _norm_cap(cap):
    cap = str(cap).strip().upper()
    if cap.startswith("CAP_"):
        cap = cap[4:]
    return cap


def _under(path, base):
    return path == base or path.startswith(base + "/")


class Facts:
    """归一化后的容器安全事实。"""

    def __init__(self, container):
        if not isinstance(container, dict) or not container.get("name"):
            raise ValueError("container is required")

        self.name = container["name"]
        self.host = container.get("host", "")
        self.image = container.get("image", "")

        self.privileged = bool(container.get("privileged"))

        raw_caps = container.get("caps", container.get("capabilities", []))
        if raw_caps is None:
            raw_caps = []
        if not isinstance(raw_caps, (list, tuple, set)):
            raise ValueError("caps must be a list")
        self.caps = sorted({_norm_cap(c) for c in raw_caps})

        raw_mounts = container.get("mounts", [])
        if raw_mounts is None:
            raw_mounts = []
        if not isinstance(raw_mounts, (list, tuple)):
            raise ValueError("mounts must be a list")
        self.mounts = [_norm_mount(m) for m in raw_mounts]

        def _flag(*keys):
            return any(bool(container[k]) for k in keys if k in container)

        self.host_pid = _flag("host_pid", "hostPid")
        self.host_ipc = _flag("host_ipc", "hostIpc")
        self.host_net = _flag("host_net", "hostNetwork", "hostNet")
        self.host_uts = _flag("host_uts", "hostUts")

        raw_sysctls = container.get("sysctls", {}) or {}
        if not isinstance(raw_sysctls, dict):
            raise ValueError("sysctls must be a dict")
        self.sysctls = {str(k): str(v) for k, v in raw_sysctls.items()}

        seccomp = str(container.get("seccomp", "default")).strip().lower()
        self.seccomp = "unconfined" if seccomp in ("unconfined", "none", "") else "default"

        apparmor = str(container.get("apparmor", "default")).strip().lower()
        self.apparmor = "unconfined" if apparmor == "unconfined" else "default"

        self._groups = None

    # ---- 能力与挂载分类 -------------------------------------------------

    def has_cap(self, cap):
        """privileged 容器拥有全部能力。"""
        return self.privileged or cap in self.caps

    @property
    def dangerous_caps(self):
        if self.privileged:
            return []
        return [c for c in self.caps if c in DANGEROUS_CAPS]

    @property
    def mount_groups(self):
        if self._groups is not None:
            return self._groups
        groups = {
            "sensitive": [],
            "device": [],
            "special_device": [],
            "proc_sys": [],
            "cgroup": [],
            "modules": [],
            "sysfs": [],
            "socket": [],
        }
        specialized = set()
        for mount in self.mounts:
            source = mount["source"]
            if source in SOCKET_PATHS:
                groups["socket"].append(mount)
                specialized.add(id(mount))
            if source in SPECIAL_DEVICES:
                groups["special_device"].append(mount)
                specialized.add(id(mount))
            elif any(source.startswith(p) for p in DEVICE_PREFIXES):
                groups["device"].append(mount)
                specialized.add(id(mount))
            if source == "/proc" or _under(source, "/proc/sys"):
                groups["proc_sys"].append(mount)
                specialized.add(id(mount))
            if _under(source, "/sys/fs/cgroup"):
                groups["cgroup"].append(mount)
                specialized.add(id(mount))
            if source == "/sys":
                groups["sysfs"].append(mount)
                specialized.add(id(mount))
            if _under(source, "/lib/modules") or _under(source, "/usr/lib/modules"):
                groups["modules"].append(mount)
                specialized.add(id(mount))
        for mount in self.mounts:
            if id(mount) in specialized:
                continue
            if any(_under(mount["source"], base) for base in SENSITIVE_PATHS):
                groups["sensitive"].append(mount)
        self._groups = groups
        return groups

    def mounts_of(self, group, writable_only=True):
        mounts = self.mount_groups[group]
        if writable_only:
            return [m for m in mounts if m["rw"]]
        return list(mounts)

    @property
    def core_pattern_pipe(self):
        value = self.sysctls.get("kernel.core_pattern", "")
        return value.startswith("|")

    # ---- 输出物 ---------------------------------------------------------

    def basis_map(self):
        """每项配置的实际取值，供单条命中规则引用，也组成完整判定依据。"""
        return {
            "privileged": {"key": "privileged", "value": self.privileged},
            "caps": {"key": "caps", "value": list(self.caps)},
            "mounts": {"key": "mounts", "value": [dict(m) for m in self.mounts]},
            "host_pid": {"key": "host_pid", "value": self.host_pid},
            "host_ipc": {"key": "host_ipc", "value": self.host_ipc},
            "host_net": {"key": "host_net", "value": self.host_net},
            "host_uts": {"key": "host_uts", "value": self.host_uts},
            "sysctls": {"key": "sysctls", "value": dict(self.sysctls)},
            "seccomp": {"key": "seccomp", "value": self.seccomp},
            "apparmor": {"key": "apparmor", "value": self.apparmor},
        }

    def basis(self):
        return list(self.basis_map().values())

    def config_hash(self):
        """配置指纹：不含 name/host/image，同配置必然同指纹。"""
        payload = {
            "privileged": self.privileged,
            "caps": self.caps,
            "mounts": sorted(
                ({"source": m["source"], "target": m["target"], "rw": m["rw"]}
                 for m in self.mounts),
                key=lambda m: (m["source"], m["target"]),
            ),
            "host_pid": self.host_pid,
            "host_ipc": self.host_ipc,
            "host_net": self.host_net,
            "host_uts": self.host_uts,
            "sysctls": self.sysctls,
            "seccomp": self.seccomp,
            "apparmor": self.apparmor,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
