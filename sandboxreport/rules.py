"""行为分类、关键行为与异常行为的规则表。

规则全部基于正则，匹配忽略大小写，保证同一份报告多次解析结论一致，
不依赖任何外部模型或网络资源。
"""

import re

NETWORK = "network"
FILE = "file"
PROCESS = "process"
REGISTRY = "registry"

# 支持的四类标准行为，顺序即汇总时的展示顺序。
CANONICAL_TYPES = (NETWORK, FILE, PROCESS, REGISTRY)

# ---------------------------------------------------------------------------
# 关键行为：分析同学最关心、需要标出发生秒数的动作模式。
# ---------------------------------------------------------------------------
KEY_RULES = {
    NETWORK: [
        re.compile(r"connect|dns query|http request|download", re.IGNORECASE),
    ],
    FILE: [
        re.compile(r"delete|encrypt|drop", re.IGNORECASE),
        re.compile(
            r"write .*(startup|system32|drivers|autorun|cron|launchd)",
            re.IGNORECASE,
        ),
    ],
    PROCESS: [
        re.compile(r"inject|create process|spawn|exec", re.IGNORECASE),
    ],
    REGISTRY: [
        re.compile(
            r"set .*(\\run\\|\\runonce\\|image file execution options)",
            re.IGNORECASE,
        ),
    ],
}

# ---------------------------------------------------------------------------
# 异常行为规则：(规则 ID, 行为类型, 动作匹配正则, 中文说明)
# 命中任意一条即把该行为条目标记为异常；异常条目同时视为关键行为。
# ---------------------------------------------------------------------------
ANOMALY_RULES = [
    (
        "ANM_NET_C2_PORT",
        NETWORK,
        re.compile(
            r"connect\s+[\w.\-]+:(?:4444|1337|8443|9001|50050|31337)\b",
            re.IGNORECASE,
        ),
        "连接疑似 C2 端口",
    ),
    (
        "ANM_NET_SUSPICIOUS_DNS",
        NETWORK,
        re.compile(
            r"dns query\s+\S+\.(?:xyz|top|ru|tk|ml|ga|cf|info)\b",
            re.IGNORECASE,
        ),
        "可疑顶级域 DNS 查询",
    ),
    (
        "ANM_FILE_RANSOM",
        FILE,
        re.compile(r"encrypt\b", re.IGNORECASE),
        "批量加密文件，疑似勒索行为",
    ),
    (
        "ANM_FILE_HOSTS",
        FILE,
        re.compile(
            r"write\s+.*(?:drivers[/\\]etc[/\\]hosts|/etc/hosts)",
            re.IGNORECASE,
        ),
        "篡改 hosts 文件",
    ),
    (
        "ANM_FILE_STARTUP",
        FILE,
        re.compile(r"write\s+.*(?:startup|autostart|启动)", re.IGNORECASE),
        "写入启动目录实现持久化",
    ),
    (
        "ANM_PROC_INJECT",
        PROCESS,
        re.compile(r"inject\b", re.IGNORECASE),
        "进程注入",
    ),
    (
        "ANM_PROC_SHELL",
        PROCESS,
        re.compile(r"cmd\.exe|powershell|/bin/sh\b|\bbash\b", re.IGNORECASE),
        "拉起命令解释器",
    ),
    (
        "ANM_PROC_PERSIST",
        PROCESS,
        re.compile(
            r"create process .*(?:sc\.exe|schtasks|systemctl|launchctl)",
            re.IGNORECASE,
        ),
        "创建持久化服务或计划任务",
    ),
    (
        "ANM_REG_RUN",
        REGISTRY,
        re.compile(r"set\s+.*\\(?:run|runonce)\\", re.IGNORECASE),
        "写入自启动注册表项",
    ),
    (
        "ANM_REG_IFEO",
        REGISTRY,
        re.compile(r"image file execution options", re.IGNORECASE),
        "IFEO 镜像劫持",
    ),
    (
        "ANM_REG_DISABLE_DEFENSE",
        REGISTRY,
        re.compile(
            r"set\s+.*(?:windows defender|firewall|disable)",
            re.IGNORECASE,
        ),
        "修改安全防护相关注册表",
    ),
]

# ---------------------------------------------------------------------------
# 静态特征 -> 预期行为类型的 API/导入表提示。
# 用于静态特征与行为结论的对账。
# ---------------------------------------------------------------------------
API_HINT_TABLE = [
    (
        NETWORK,
        re.compile(
            r"socket|connect|send|recv|wininet|winhttp|internetopen|"
            r"urldownload|wsastartup",
            re.IGNORECASE,
        ),
    ),
    (
        FILE,
        re.compile(
            r"createfile|writefile|deletefile|fopen|fwrite|removefile",
            re.IGNORECASE,
        ),
    ),
    (
        PROCESS,
        re.compile(
            r"createprocess|shellexecute|winexec|virtualalloc|"
            r"createremotethread|openprocess",
            re.IGNORECASE,
        ),
    ),
    (
        REGISTRY,
        re.compile(r"regopenkey|regsetvalue|regcreatekey|shreg", re.IGNORECASE),
    ),
]
