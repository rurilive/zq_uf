"""已知壳家族的结构/行为特征库。

识别不能只靠区段名（区段名可以随意改），每个家族同时给出：

- section_names：区段名特征，命中即强信号，但只是信号之一；
- structural：与区段名无关的结构规则，改名样本照样能对上；
- behavior：运行时行为特征；
- weight：结构规则命中时的得分，凑够阈值即可在没有区段名时认出家族。
"""

# 高熵阈值：熵 >= 该值视为区段内容被压缩/加密
HIGH_ENTROPY = 7.0

# 只靠运行时解析 API 时常见的最小导入集
LOADER_IMPORTS = frozenset(["loadlibrarya", "loadlibraryw", "getprocaddress"])

# 能自行解出真实 API 的家族，导入表通常只剩加载器三件套
MINIMAL_IMPORT_FAMILIES = frozenset(["upx", "aspack", "petite"])

# 家族 -> 区段名特征（小写）
FAMILY_SECTION_NAMES = {
    "upx": ["upx0", "upx1"],
    "aspack": [".aspack", ".adata"],
    "themida": [".themida"],
    "petite": [".petite"],
}

# 家族 -> 结构规则（取值见 features 里对应的键），全部与区段名无关
FAMILY_STRUCTURAL_RULES = {
    "upx": {
        "writable_zero_raw": True,        # 存在可写、原始大小为 0 的空白解压区（UPX0 改名也还在）
        "entry_in_last_section": True,    # 入口点落在最后一个区段
        "minimal_imports": True,          # 导入表只剩加载器 API
    },
    "aspack": {
        "high_entropy_section": True,
        "entry_in_last_section": True,
        "minimal_imports": True,
    },
    "themida": {
        "rwx_section": True,              # 存在 RWX 区段
        "high_entropy_section": True,
        "stolen_entry": True,             # 入口不在常规可执行区段（stolen bytes 风格）
    },
    "petite": {
        "high_entropy_section": True,
        "entry_in_last_section": True,
        "minimal_imports": True,
    },
}

# 每条结构规则命中时的得分；凑够 STRUCTURAL_MATCH_SCORE 即可在无区段名时判定家族
STRUCTURAL_WEIGHTS = {
    # 可写且 raw_size=0 的空白解压区是 UPX 区分性很强的结构指纹，权重高于普通信号
    "writable_zero_raw": 2,
    "entry_in_last_section": 1,
    "minimal_imports": 1,
    "high_entropy_section": 1,
    "rwx_section": 1,
    "stolen_entry": 2,
}

STRUCTURAL_MATCH_SCORE = 3

# 家族 -> 运行时行为特征（小写）
FAMILY_BEHAVIORS = {
    "upx": ["alloc_rwx"],
    "aspack": ["alloc_rwx"],
    "themida": ["anti_debug", "anti_vm"],
    "petite": ["alloc_rwx"],
}

# 家族默认脱壳参数，可被 set_unpack_params 覆盖/补充
DEFAULT_UNPACK_PARAMS = {
    "upx": {
        "tool": "upx -d",
        "method": "static_unpack",
        "bypass": [],
        "notes": "标准 UPX 可直接静态还原；自定义魔数时先修 magic 再脱",
    },
    "aspack": {
        "tool": "aspack-die",
        "method": "static_unpack",
        "bypass": [],
        "notes": "按 OEP 抓取后重建导入表",
    },
    "petite": {
        "tool": "petite-unpacker",
        "method": "static_unpack",
        "bypass": [],
        "notes": "等待解压完成后 dump，再修 IAT",
    },
    "themida": {
        "tool": "manual+x64dbg",
        "method": "dynamic_unpack",
        "bypass": ["anti_debug", "anti_vm"],
        "notes": "必须先过反调试/反虚拟机，再跟踪 OEP",
    },
}

# 脱壳阻断行为 -> (机器可读原因码, 中文说明)。参数里的 bypass 能解掉对应项。
UNPACK_BLOCKERS = {
    "anti_debug": ("anti_debug", "样本带反调试，脱壳参数未包含 anti_debug 绕过"),
    "anti_vm": ("anti_vm", "样本带反虚拟机，脱壳参数未包含 anti_vm 绕过"),
    "stolen_bytes": ("stolen_bytes", "入口指令被抽走（stolen bytes），当前参数无法重建 OEP"),
    "encrypted_iat": ("encrypted_iat", "导入表加密，当前参数不支持 IAT 重建"),
}
