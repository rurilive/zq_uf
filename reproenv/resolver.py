"""依赖解析：把报告里的依赖声明解析成包记录。

真实场景里这里会走包管理器/私有源；本工具无外部依赖，默认 resolver
在本地模拟拉取，并约定一套可离线触发失败的规则，方便复现"拉取失败
停在准备阶段"这条链路：

* 报告里 ``offline`` 为真 -> 所有依赖都拉不到（模拟断网/源不可用）；
* 包名以 ``missing-``、``fail-``、``nonexistent`` 开头 -> 包不存在；
* 版本固定为 ``0.0.0`` -> 该版本不存在；
* 自定义 ``resolver=callable`` 可完全接管，抛 ``DepResolveError`` 即可；
  返回值需要是带 ``name`` 的字典，可选 ``version``/``spec``/``size``。

复现要求版本可钉死：范围约束（``>=``/``~=`` 等）直接拒绝，避免
"装错一个包结论就对不上"。
"""

import hashlib

from .errors import DepResolveError, ReportError

# 常见包的模拟体积（字节），未知包按包名确定性生成，保证可重复。
_SIZE_CATALOG = {
    "requests": 120_000,
    "flask": 90_000,
    "urllib3": 80_000,
    "jinja2": 70_000,
    "numpy": 4_200_000,
}

_FAIL_PREFIXES = ("missing-", "fail-", "nonexistent")
_RANGE_OPS = (">=", "<=", "~=", "!=", ">", "<")


def parse_spec(spec):
    """``requests==2.31.0`` -> ``(name, version, spec)``；未钉版本时版本为空串。"""
    if not isinstance(spec, str) or not spec.strip():
        raise ReportError("依赖声明必须是非空字符串，收到 %r" % (spec,))
    text = spec.strip()
    if "==" in text:
        name, version = text.split("==", 1)
    elif any(op in text for op in _RANGE_OPS):
        raise ReportError("依赖必须钉死版本（name==version），收到 %r" % spec)
    else:
        name, version = text, ""
    name = name.strip()
    version = version.strip()
    if not name:
        raise ReportError("依赖缺少包名，收到 %r" % (spec,))
    return name, version, text


def package_size(name):
    """模拟包体积：目录里的包用固定值，其余按包名确定性生成。"""
    if name in _SIZE_CATALOG:
        return _SIZE_CATALOG[name]
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    return 50_000 + int(digest[:8], 16) % 200_000


def default_resolver(spec, report):
    """本地模拟拉取。成功返回包记录字典，失败抛 DepResolveError。"""
    name, version, raw = parse_spec(spec)
    if report.get("offline"):
        raise DepResolveError(name, version, "源不可用，依赖拉取失败", raw)
    if name.startswith(_FAIL_PREFIXES):
        raise DepResolveError(name, version, "包不存在", raw)
    if version == "0.0.0":
        raise DepResolveError(name, version, "该版本不存在", raw)
    return {
        "name": name,
        "version": version,
        "spec": raw,
        "size": package_size(name),
        "status": "resolved",
    }
