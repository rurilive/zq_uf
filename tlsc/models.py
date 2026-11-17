"""数据模型：登记的入口（Entry）与核查发现的弱项（WeakItem）。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Optional

# 严重程度，从高到低；排序时直接用下标
SEVERITY_ORDER = ("critical", "high", "medium", "low")
SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITY_ORDER)}

# 弱项分类
CATEGORY_PROTOCOL = "protocol"   # 协议版本
CATEGORY_CIPHER = "cipher"       # 套件强度
CATEGORY_CERT = "cert"           # 证书问题
CATEGORY_REDIRECT = "redirect"   # 重定向


@dataclass(frozen=True)
class Entry:
    """同一域名下的一个入口（主入口、备入口、灰度、HTTP 入口等各算一项）。

    纯登记事实，不做判断；判弱规则在 ``tlsc.rules`` 里。
    """

    entry_id: str
    address: str
    port: int = 443
    scheme: str = "https"
    # 证书剩余有效期（天）；HTTP 明文入口为 None
    days_left: Optional[int] = None
    # 签发链，顺序为 根 CA -> 中间 CA -> 本站证书；长度 1 视为自签名
    issuer_chain: tuple[str, ...] = ()
    # 协商出的协议版本与加密套件；明文入口为 None
    protocol: Optional[str] = None
    cipher: Optional[str] = None
    # 重定向：redirect 为跳转状态码（"301"/"302"），None 表示不跳转
    redirect: Optional[str] = None
    redirect_target: Optional[str] = None

    def fingerprint(self) -> str:
        """入口配置指纹：任一字段变化都会变，用于增量重查。"""
        payload = dataclasses.asdict(self)
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WeakItem:
    """一条弱项：分类 + 严重程度 + 实际取值 + 期望取值。"""

    category: str
    severity: str
    item: str
    actual: object
    expected: object

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)
