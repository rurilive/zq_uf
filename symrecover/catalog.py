"""已知库特征目录。

每条目描述一个公开符号表里的函数可能露出的特征：
- constants: 函数体内出现的魔数（库特征，可信度最高）
- strings:   函数引用的特征字符串
- callees:   函数必然调用的下游函数名（外部导入名或已恢复的本地函数名）

目录只存事实数据，匹配规则在 symbols.py 里。分析同学可以用
load_catalog() 往里追加自己从开源版本整理出来的条目。
"""

# 内置条目刻意选真实常见特征，评审时可逐条对照开源源码核实。
DEFAULT_CATALOG = [
    {
        "name": "MD5_Init",
        "library": "libcrypto",
        "constants": [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476],
        "strings": [],
        "callees": ["memset"],
    },
    {
        # 比 MD5 多一个 0xC3D2E1F0，用来验证子集匹配不会误判
        "name": "SHA1_Init",
        "library": "libcrypto",
        "constants": [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0],
        "strings": [],
        "callees": ["memset"],
    },
    {
        "name": "CRC32",
        "library": "zlib",
        "constants": [0xEDB88320],
        "strings": [],
        "callees": [],
    },
    {
        "name": "admin_login",
        "library": "routerd",
        "constants": [],
        "strings": ["admin", "login failed"],
        "callees": [],
    },
    {
        "name": "load_key_file",
        "library": "routerd",
        "constants": [],
        "strings": ["/tmp/key"],
        "callees": ["memcpy"],
    },
    {
        # 只靠调用关系推断的条目：必然先做一次 MD5 初始化
        "name": "digest_session",
        "library": "routerd",
        "constants": [],
        "strings": [],
        "callees": ["MD5_Init"],
    },
    {
        "name": "strdup",
        "library": "libc",
        "constants": [],
        "strings": [],
        "callees": ["malloc", "strlen"],
    },
]
