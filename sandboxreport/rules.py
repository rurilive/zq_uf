"""行为归类、关键行为、异常行为与静态特征对账规则。

规则全部走关键词匹配，保持确定性、可测试，不引入任何模型判断，
分析同学后续可以直接在这里增删关键词。
"""

import re

# 四类规范行为类型
NETWORK = "network"
FILE = "file"
PROCESS = "process"
REGISTRY = "registry"

TYPES = (NETWORK, FILE, PROCESS, REGISTRY)

# 分节报告里的节标题关键词 -> 规范类型
_SECTION_KEYWORDS = (
    (NETWORK, ("network", "网络", "connection", "dns", "traffic")),
    (FILE, ("file", "文件", "filesystem", "fs")),
    (PROCESS, ("process", "进程", "thread")),
    (REGISTRY, ("registry", "注册表", "reg")),
)

# 关键行为（高信号动作）关键词
_KEYWORD_RULES = (
    (NETWORK, ("connect", "dns", "http", "download", "upload", "request",
               "连接", "请求", "下载")),
    (FILE, ("write", "create", "delete", "drop", "rename", "写", "创建", "删除")),
    (PROCESS, ("create", "spawn", "exec", "inject", "scheduled task",
               "创建进程", "注入", "执行")),
    (REGISTRY, ("set", "create", "delete", "run", "persist", "设置", "创建")),
)

# 异常行为：(行为类型, 匹配正则, 异常原因)
_ANOMALY_RULES = (
    (NETWORK, re.compile(r"connect\s+\d{1,3}(?:\.\d{1,3}){3}"),
     "outbound network connection"),
    (NETWORK, re.compile(r"dns(?:\s|query)", re.IGNORECASE),
     "dns lookup"),
    (FILE, re.compile(r"(?:write|create|drop)", re.IGNORECASE),
     "file write/drop"),
    (FILE, re.compile(r"\\startup\\|/\.config/autostart/|currentversion\\run",
                      re.IGNORECASE),
     "writes to autostart/persistence location"),
    (FILE, re.compile(r"delete", re.IGNORECASE),
     "file deletion"),
    (PROCESS, re.compile(r"inject|hollow", re.IGNORECASE),
     "process injection"),
    (PROCESS, re.compile(r"powershell|cmd(?:\.exe)?\b|/bin/sh\b|wscript|cscript",
                         re.IGNORECASE),
     "spawns shell/script interpreter"),
    (PROCESS, re.compile(r"create|spawn|exec", re.IGNORECASE),
     "process creation"),
    (REGISTRY, re.compile(r"currentversion\\run", re.IGNORECASE),
     "registry persistence (Run key)"),
    (REGISTRY, re.compile(r"delete", re.IGNORECASE),
     "registry deletion"),
)


def normalize_action(action):
    """归并动作文本：小写化并压缩空白，作为重复行为的归并键。"""
    return re.sub(r"\s+", " ", str(action).strip().lower())


def section_type(title):
    """根据节标题判断行为类型，认不出来返回 None。"""
    if title is None:
        return None
    lowered = str(title).strip().lower()
    for behavior_type, keywords in _SECTION_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return behavior_type
    return None


def anomaly_reason(behavior_type, action):
    """命中异常规则时返回原因字符串，否则返回 None。"""
    for rule_type, pattern, reason in _ANOMALY_RULES:
        if rule_type == behavior_type and pattern.search(str(action)):
            return reason
    return None


def is_key(behavior_type, action):
    """关键行为：高信号动作，时间线上需要标出发生秒数。"""
    lowered = str(action).lower()
    for rule_type, keywords in _KEYWORD_RULES:
        if rule_type == behavior_type and any(k in lowered for k in keywords):
            return True
    return False


def check_static(static, events):
    """静态特征与实际行为对账。

    只在静态特征给出明确断言（True/False）时核对，没给的不臆测：
      * network 能力声明 vs 实际网络行为
      * 文件写入能力声明 vs 实际写入/创建
      * 持久化声明 vs 自启动/Run 键等持久化行为
    """
    static = static or {}
    mismatches = []

    observed = {t: [e["action"] for e in events if e["type"] == t]
                for t in TYPES}
    has_network = bool(observed[NETWORK])
    has_file_write = any(
        re.search(r"write|create|drop|写|创建", action, re.IGNORECASE)
        for action in observed[FILE]
    )
    has_persistence = any(
        anomaly_reason(REGISTRY, action) == "registry persistence (Run key)"
        for action in observed[REGISTRY]
    ) or any(
        "autostart" in action.lower() or "startup" in action.lower()
        for action in observed[FILE]
    )

    def compare(flag_name, observed_value, behavior_label):
        if flag_name not in static:
            return
        declared = static[flag_name]
        if declared is not None and bool(declared) != bool(observed_value):
            mismatches.append({
                "feature": flag_name,
                "declared": declared,
                "observed": observed_value,
                "detail": "static feature claims {neg}{label}, but no {label} "
                          "behavior observed".format(
                              neg="" if observed_value else "no ",
                              label=behavior_label),
            })

    compare("network", has_network, "network")
    compare("file_write", has_file_write, "file write")
    compare("persistence", has_persistence, "persistence")
    return mismatches
