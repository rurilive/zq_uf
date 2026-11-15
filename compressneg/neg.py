"""按站点协商压缩与字符编码。

原实现对所有站点都发同一套 ``accept-encoding: gzip``，响应固定按 utf-8
解码，于是出现三类问题：支持压缩的站点内容变成乱码、不支持压缩的站点
直接拒绝请求、br/zstd 的解压结果被当成二进制丢弃。本模块把压缩和字符集
改成按站点协商：

* **先探测再请求**：未知站点首次访问发全量 ``accept-encoding`` 探测，
  响应到达后按实际的 ``content-encoding`` 把结果记在站点名下，之后只发
  协商过的方式；站点拒绝压缩、解压失败或协商自检失败时退回 ``identity``。
* **按实际编码解码**：``content-encoding`` 支持多层（如 ``gzip, br``），
  按与声明相反的顺序逐层解压。字符集按 BOM → 响应头 → ``<meta>`` →
  内容嗅探 判定；响应头没写编码时从内容判断，不再硬按 utf-8 解。
* **失败单独标出**：解压失败保留原始字节并标 ``decompress_failed``，
  绝不会被当成空页面；原始响应一直保留到 ``clear_failed()`` 排查结束。
  空响应体给出明确的 ``empty`` 状态，而不是静默的空字符串。
* **记录只留最近一次**：每个站点只保留最近一次协商结果。人工
  ``override()`` 立即锁定，之后自动协商不再改回去；站点改版时用
  ``unlock()`` 解锁重探。
* **自检隔离**：新站点接入前用 ``selfcheck()`` 跑一次协商自检，
  失败或某个解码器不可用只影响这个站点，其他站点照常。

``decode()`` 的返回值统一给出：``negotiation``（协商结果）、``encoding``
（实际字符集）、``content_encoding`` / ``layers`` / ``decompression``
（实际压缩方式与是否解压成功）、``text`` 和 ``failure``（失败原因）。
"""

from __future__ import annotations

import codecs
import ctypes
import ctypes.util
import datetime as _dt
import gzip
import json
import os
import re
import tempfile
import zlib
from dataclasses import dataclass, field

__all__ = [
    "CodecError",
    "Negotiator",
    "StoreError",
    "clear_failed",
    "decode",
    "detect_charset",
    "failed_responses",
    "headers",
    "offer_list",
    "override",
    "record",
    "records",
    "selfcheck",
    "unlock",
]

# 无外部依赖：可选的 brotli / zstandard 第三方包、Python 3.14 的
# compression.zstd 都只是快捷方式，本机没有时退回 ctypes 调系统库。
try:  # pragma: no cover - 取决于安装环境
    import brotli as _brotli
except ImportError:  # pragma: no cover
    _brotli = None

try:  # pragma: no cover - Python 3.14+
    from compression import zstd as _pyzstd
except ImportError:  # pragma: no cover
    _pyzstd = None

try:  # pragma: no cover
    import zstandard as _zstandard
except ImportError:  # pragma: no cover
    _zstandard = None

try:  # pragma: no cover
    import brotlicffi as _brotlicffi
except ImportError:  # pragma: no cover
    _brotlicffi = None


# ----------------------------------------------------------------------
# 错误类型
# ----------------------------------------------------------------------

class CodecError(RuntimeError):
    """压缩方式不可用或解压失败。"""


class StoreError(RuntimeError):
    """协商记录的持久化读写失败。"""


# ----------------------------------------------------------------------
# 压缩解码器注册表
# ----------------------------------------------------------------------

# 客户端优先顺序。只有在本地确实能解压的方式才会出现在 accept-encoding
# 里——否则站点按它压缩后我们解不出来，正是原来的故障。
OFFER_ORDER = ("gzip", "deflate", "br", "zstd")

_MAX_FAILURES = 20          # 每个站点保留的失败原因条数
_MAX_KEPT_RESPONSES = 20    # 每个站点保留的解压失败原始响应条数

_now = _dt.datetime.now     # 时间 seam，测试里可替换


class Codec:
    """一种 content-encoding 的解码器（编码器仅供自检回环使用）。"""

    __slots__ = ("name", "decompress", "encode", "detail")

    def __init__(self, name, decompress, encode=None, detail=""):
        self.name = name
        self.decompress = decompress
        self.encode = encode
        self.detail = detail

    @property
    def available(self):
        return self.decompress is not None


def _decode_identity(data):
    return bytes(data)


def _encode_identity(data):
    return bytes(data)


def _decode_deflate(data):
    """deflate 既有带 zlib 头的，也有裸 deflate 的，两种都试。"""
    try:
        return zlib.decompress(data)
    except zlib.error:
        return zlib.decompress(data, -zlib.MAX_WBITS)


def _ctypes_library(name):
    """按库名加载共享库，找不到返回 None。"""
    try:
        path = ctypes.util.find_library(name)
    except (OSError, TypeError):  # pragma: no cover
        return None
    if not path:
        return None
    try:
        return ctypes.CDLL(path)
    except OSError:  # pragma: no cover
        return None


class _ZstdInBuffer(ctypes.Structure):
    _fields_ = [("src", ctypes.c_void_p),
                ("size", ctypes.c_size_t),
                ("pos", ctypes.c_size_t)]


class _ZstdOutBuffer(ctypes.Structure):
    _fields_ = [("dst", ctypes.c_void_p),
                ("size", ctypes.c_size_t),
                ("pos", ctypes.c_size_t)]


def _ctypes_zstd_codec():  # pragma: no cover - 依赖系统库
    lib = _ctypes_library("zstd")
    if lib is None:
        return None
    try:
        lib.ZSTD_createDStream.restype = ctypes.c_void_p
        lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
        lib.ZSTD_freeDStream.restype = ctypes.c_size_t
        lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
        lib.ZSTD_initDStream.restype = ctypes.c_size_t
        lib.ZSTD_DStreamOutSize.restype = ctypes.c_size_t
        lib.ZSTD_decompressStream.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        lib.ZSTD_decompressStream.restype = ctypes.c_size_t
        lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
        lib.ZSTD_isError.restype = ctypes.c_uint
        lib.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
        lib.ZSTD_getErrorName.restype = ctypes.c_char_p
        lib.ZSTD_compressBound.argtypes = [ctypes.c_size_t]
        lib.ZSTD_compressBound.restype = ctypes.c_size_t
        lib.ZSTD_compress.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.c_int]
        lib.ZSTD_compress.restype = ctypes.c_size_t
    except AttributeError:  # pragma: no cover
        return None

    def decompress(data):
        stream = lib.ZSTD_createDStream()
        if not stream:
            raise CodecError("ZSTD_createDStream 失败")
        try:
            code = lib.ZSTD_initDStream(stream)
            if lib.ZSTD_isError(code):
                raise CodecError(_zstd_error(lib, code))
            source = ctypes.create_string_buffer(bytes(data), len(data))
            inbuf = _ZstdInBuffer(ctypes.cast(source, ctypes.c_void_p),
                                  len(data), 0)
            out_size = lib.ZSTD_DStreamOutSize() or 131072
            chunks = []
            while True:
                target = ctypes.create_string_buffer(out_size)
                outbuf = _ZstdOutBuffer(ctypes.cast(target, ctypes.c_void_p),
                                        out_size, 0)
                before = inbuf.pos
                code = lib.ZSTD_decompressStream(
                    stream, ctypes.byref(outbuf), ctypes.byref(inbuf))
                if lib.ZSTD_isError(code):
                    raise CodecError(_zstd_error(lib, code))
                chunks.append(target.raw[:outbuf.pos])
                if code == 0:
                    # 一帧结束；后面若还有拼接帧会继续消费输入。
                    if inbuf.pos >= inbuf.size:
                        return b"".join(chunks)
                    continue
                if inbuf.pos >= inbuf.size and inbuf.pos == before \
                        and outbuf.pos == 0:
                    raise CodecError("zstd 数据被截断")
        finally:
            lib.ZSTD_freeDStream(stream)

    def encode(data):
        source = ctypes.create_string_buffer(bytes(data), len(data))
        bound = lib.ZSTD_compressBound(len(data)) or (len(data) + 64)
        target = ctypes.create_string_buffer(bound)
        code = lib.ZSTD_compress(target, bound, source, len(data), 3)
        if lib.ZSTD_isError(code):
            raise CodecError(_zstd_error(lib, code))
        return target.raw[:code]

    return decompress, encode, "ctypes:libzstd"


def _zstd_error(lib, code):  # pragma: no cover - 依赖系统库
    try:
        name = lib.ZSTD_getErrorName(code)
        return name.decode("utf-8", "replace") if name else "zstd error"
    except Exception:  # noqa: BLE001 - 错误名取不到时不影响主流程
        return "zstd error"


def _ctypes_brotli_codec():  # pragma: no cover - 依赖系统库
    lib = _ctypes_library("brotlidec")
    if lib is None:
        return None
    try:
        lib.BrotliDecoderDecompress.argtypes = [
            ctypes.c_size_t, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p]
        lib.BrotliDecoderDecompress.restype = ctypes.c_int
    except AttributeError:
        return None

    def decompress(data):
        # BrotliDecoderDecompress 返回 0=ERROR 1=SUCCESS 2=NEEDS_MORE_INPUT
        # 3=NEEDS_MORE_OUTPUT。只有输出不够才放大缓冲，其余情况直接失败，
        # 避免对损坏数据反复分配大块内存。
        capacity = max(65536, len(data) * 8)
        limit = 256 * 1024 * 1024
        source = ctypes.create_string_buffer(bytes(data), len(data))
        while True:
            target = ctypes.create_string_buffer(capacity)
            size = ctypes.c_size_t(capacity)
            result = lib.BrotliDecoderDecompress(
                len(data), source, ctypes.byref(size), target)
            if result == 1:  # BROTLI_DECODER_RESULT_SUCCESS
                return target.raw[:size.value]
            if result != 3 or capacity >= limit:
                raise CodecError("brotli 解压失败（数据损坏或输出过大）")
            capacity = min(capacity * 4, limit)

    encode = None
    enc_lib = _ctypes_library("brotlienc")
    if enc_lib is not None:
        try:
            enc_lib.BrotliEncoderMaxCompressedSize.argtypes = [ctypes.c_size_t]
            enc_lib.BrotliEncoderMaxCompressedSize.restype = ctypes.c_size_t
            enc_lib.BrotliEncoderCompress.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_size_t,
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                ctypes.c_void_p]
            enc_lib.BrotliEncoderCompress.restype = ctypes.c_int

            def encode(data):  # noqa: F811 - 覆盖上面的占位
                raw = bytes(data)
                source = ctypes.create_string_buffer(raw, len(raw))
                capacity = enc_lib.BrotliEncoderMaxCompressedSize(len(raw)) or 64
                target = ctypes.create_string_buffer(capacity)
                size = ctypes.c_size_t(capacity)
                ok = enc_lib.BrotliEncoderCompress(
                    5, 22, 0, len(raw), source, ctypes.byref(size), target)
                if ok != 1:
                    raise CodecError("brotli 编码失败")
                return target.raw[:size.value]
        except AttributeError:  # pragma: no cover
            encode = None

    return decompress, encode, "ctypes:libbrotli"


def _brotli_codec():
    for module, label in ((_brotli, "python:brotli"),
                          (_brotlicffi, "python:brotlicffi")):
        if module is not None and hasattr(module, "decompress"):
            return (module.decompress,
                    getattr(module, "compress", None), label)
    found = _ctypes_brotli_codec()
    if found:
        return found
    return None, None, "缺少 brotli 解码器"


def _zstd_codec():
    if _pyzstd is not None and hasattr(_pyzstd, "decompress"):
        return (_pyzstd.decompress,
                getattr(_pyzstd, "compress", None), "stdlib:compression.zstd")
    if _zstandard is not None and hasattr(_zstandard, "ZstdDecompressor"):
        compressor = _zstandard.ZstdCompressor()

        def decompress(data):
            return _zstandard.ZstdDecompressor().decompress(data)

        return decompress, compressor.compress, "python:zstandard"
    found = _ctypes_zstd_codec()
    if found:
        return found
    return None, None, "缺少 zstd 解码器"


def _build_codecs():
    table = {
        "identity": Codec("identity", _decode_identity, _encode_identity,
                          "passthrough"),
        "gzip": Codec("gzip", gzip.decompress, gzip.compress, "stdlib:gzip"),
        "deflate": Codec("deflate", _decode_deflate, zlib.compress,
                         "stdlib:zlib"),
    }
    br_decompress, br_encode, br_detail = _brotli_codec()
    table["br"] = Codec("br", br_decompress, br_encode, br_detail)
    zstd_decompress, zstd_encode, zstd_detail = _zstd_codec()
    table["zstd"] = Codec("zstd", zstd_decompress, zstd_encode, zstd_detail)
    return table


_CODECS = _build_codecs()

# content-encoding 的常见别名
_ALIASES = {
    "x-gzip": "gzip",
    "x-deflate": "deflate",
    "zst": "zstd",
    "br": "br",
}


def offer_list():
    """本机确实能解压、可以写进 accept-encoding 的压缩方式。"""
    return [name for name in OFFER_ORDER
            if _CODECS[name].available]


def _codec(name):
    canonical = _ALIASES.get(name, name)
    return _CODECS.get(canonical), canonical


# ----------------------------------------------------------------------
# 字符集探测
# ----------------------------------------------------------------------

# BOM 最明确，先看。UTF-32 的 BOM 以 UTF-16 的 BOM 为前缀，必须排前面。
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

_CHARSET_PARAM = re.compile(
    r"""charset\s*=\s*["']?([A-Za-z0-9_.:+-]+)""", re.I)
_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_.:+-]+)""", re.I)
_XML_ENCODING = re.compile(
    rb"""<\?xml[^>]+encoding\s*=\s*["']([A-Za-z0-9_.:+-]+)""", re.I)


def _normalize_charset(name):
    """把字符集名规范化，未知名字返回 None。"""
    if not name:
        return None
    try:
        return codecs.lookup(str(name).strip()).name
    except (LookupError, ValueError):
        return None


def detect_charset(body, content_type="", hinted=None):
    """判断正文的字符集，返回 ``(字符集, 依据)``。

    依据取值：``bom`` / ``header`` / ``meta`` / ``content`` / ``remembered``
    / ``unknown``。判定顺序为 BOM → 响应头 → ``<meta>`` / XML 声明 →
    严格 utf-8 → 该站点记下的字符集 → gb18030，最后才 ``unknown``，
    调用方据此标记解码失败，而不是硬按 utf-8 替换成问号。
    """
    data = _as_bytes(body)

    for bom, name in _BOMS:
        if data.startswith(bom):
            return name, "bom"

    name = _normalize_charset(
        (_CHARSET_PARAM.search(content_type or "") or [None, None])[1])
    if name:
        return name, "header"

    head = data[:4096]
    match = _META_CHARSET.search(head) or _XML_ENCODING.search(head)
    if match:
        name = _normalize_charset(match.group(1).decode("ascii", "ignore"))
        if name:
            return name, "meta"

    try:
        data.decode("utf-8")
        return "utf-8", "content"
    except UnicodeDecodeError:
        pass

    # 只有严格 utf-8 解不开时才相信站点记下的编码，避免把本来正确的
    # utf-8 内容按过期的 gbk 记录解成乱码。
    name = _normalize_charset(hinted)
    if name and name != "utf-8":
        try:
            data.decode(name)
            return name, "remembered"
        except (UnicodeDecodeError, LookupError):
            pass

    try:
        data.decode("gb18030")
        return "gb18030", "content"
    except UnicodeDecodeError:
        return "utf-8", "unknown"


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------

def _as_bytes(value):
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError("body 必须是 bytes，收到 %s" % type(value).__name__)


def _header(headers_, name, default=""):
    if not headers_:
        return default
    target = name.lower()
    for key, value in headers_.items():
        if str(key).lower() != target:
            continue
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value)
        return default if value is None else value
    return default


def _parse_codings(raw):
    """把 content-encoding 头拆成规范化的编码列表（保持声明顺序）。"""
    names = []
    for part in str(raw or "").split(","):
        token = part.strip().lower()
        if not token:
            continue
        token = _ALIASES.get(token, token)
        if token != "identity":
            names.append(token)
    return names


def _display_codings(codings):
    return ", ".join(codings) if codings else "identity"


def _source_for(state, was_probed):
    """协商结论来自哪里：人工覆盖 / 失败回退 / 记忆 / 本次探测。"""
    if state.locked:
        return "override"
    if state.fallback:
        return "fallback"
    if was_probed:
        return "remembered"
    return "probe"


# ----------------------------------------------------------------------
# 站点状态
# ----------------------------------------------------------------------

@dataclass
class SiteState:
    """一个站点的协商记录，只保留最近一次结果。

    ``locked_fields`` 记录人工锁定的字段（``accept_encoding`` /
    ``charset``）。只锁字符集时压缩方式仍可自动协商，反之亦然——覆盖一项
    不应该顺带冻结另一项。
    """

    site: str | None = None
    accept_encoding: str | None = None   # 下次请求要发的 accept-encoding
    charset: str | None = None           # 协商出的字符集
    probed: bool = False                 # 是否已经从真实响应学到结论
    locked_fields: list = field(default_factory=list)  # 人工锁定的字段
    fallback: bool = False               # 是否因失败退回 identity
    fallback_reason: str | None = None
    offered: list = field(default_factory=list)   # 探测时实际发出的方式
    disabled: list = field(default_factory=list)  # 本机/本站自检淘汰的方式
    failures: list = field(default_factory=list)  # 最近的失败原因
    last: dict | None = None             # 最近一次协商/解码结果
    selfcheck: dict | None = None        # 最近一次协商自检
    requests: int = 0
    last_seen: str | None = None

    @property
    def locked(self):
        """是否有人工锁定（任一字段）。"""
        return bool(self.locked_fields)

    def locks(self, field_name):
        return field_name in self.locked_fields


def _new_state(site):
    return SiteState(site=site)


# ----------------------------------------------------------------------
# 可选的持久化（不传 store_path 时完全在内存里）
# ----------------------------------------------------------------------

_SERIAL_FIELDS = ("accept_encoding", "charset", "probed", "locked_fields",
                  "fallback", "fallback_reason", "offered", "disabled",
                  "failures", "last", "selfcheck", "requests", "last_seen")


class _Store:
    """把协商记录写成 JSON 快照，原子替换，避免读到半个文件。"""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.directory = os.path.dirname(self.path) or "."

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise StoreError("读取协商记录失败 %s: %s" % (self.path, exc))
        except ValueError as exc:
            raise StoreError("协商记录不是合法 JSON %s: %s" % (self.path, exc))
        if not isinstance(payload, dict):
            raise StoreError("协商记录格式错误 %s: 顶层必须是对象" % self.path)
        sites = payload.get("sites", {})
        if not isinstance(sites, dict):
            raise StoreError("协商记录格式错误 %s: sites 必须是对象" % self.path)
        return sites

    def save(self, sites):
        payload = {"version": 1,
                   "saved_at": _now().isoformat(timespec="seconds"),
                   "sites": sites}
        try:
            os.makedirs(self.directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".compressneg-", suffix=".tmp",
                                       dir=self.directory)
        except OSError as exc:
            raise StoreError("协商记录目录不可写 %s: %s"
                             % (self.directory, exc))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise StoreError("写入协商记录失败 %s: %s" % (self.path, exc))


# ----------------------------------------------------------------------
# 协商器
# ----------------------------------------------------------------------

class Negotiator:
    """按站点保存压缩与字符集协商结果，站点之间互不影响。

    默认只在内存里记录；``Negotiator(store_path=...)`` 会把记录原子落盘，
    进程重启后仍按站点沿用，适合采集任务分批跑的场景。
    """

    def __init__(self, store_path=None):
        self._sites = {}
        self._failed_raw = {}   # site -> [解压失败的原始响应]
        self._store = _Store(store_path) if store_path else None
        if self._store is not None:
            self._load()

    # -- 状态存取 ------------------------------------------------------

    def _state(self, site):
        key = site if site is not None else None
        state = self._sites.get(key)
        if state is None:
            state = _new_state(key)
            self._sites[key] = state
        return state

    def _load(self):
        try:
            stored = self._store.load()
        except StoreError:
            raise
        for name, payload in stored.items():
            if not isinstance(payload, dict):
                continue
            state = _new_state(name)
            for key in _SERIAL_FIELDS:
                if key in payload:
                    setattr(state, key, payload[key])
            # 兼容旧快照里的整体 locked 标记
            if payload.get("locked") and not state.locked_fields:
                state.locked_fields = ["accept_encoding", "charset"]
            self._sites[name] = state

    def _save(self):
        if self._store is None:
            return
        self._store.save({name: {key: getattr(state, key)
                                 for key in _SERIAL_FIELDS}
                          for name, state in self._sites.items()})

    # -- 请求头 --------------------------------------------------------

    def _offer(self, state):
        offered = [name for name in offer_list()
                   if name not in (state.disabled or [])]
        return offered or []

    def headers(self, site):
        """返回该站点本次请求要用的 ``accept-encoding``。

        未知站点发全量探测；已探测或已人工锁定的站点只发结论值；本机没有
        可用解码器或站点失败回退过时发 ``identity``。
        """
        state = self._state(site)
        if state.accept_encoding and (state.probed or state.locks(
                "accept_encoding")):
            return {"accept-encoding": state.accept_encoding}
        offered = self._offer(state)
        if not offered:
            return {"accept-encoding": "identity"}
        state.offered = offered
        return {"accept-encoding": ", ".join(offered)}

    # -- 解码 ----------------------------------------------------------

    def decode(self, site_or_response, response=None):
        """解压并按实际字符集解码一条响应。

        参数既可写成 ``decode(site, response)``，也可写成
        ``decode(response)``（无站点记忆的一次性解码）。
        """
        site, response = _split_args(site_or_response, response)
        if not isinstance(response, dict):
            raise TypeError("response 必须是 dict，包含 body/headers/status")

        state = self._state(site) if site is not None else _new_state(None)
        was_probed = state.probed

        handle = response.get("headers") or {}
        body = _as_bytes(response.get("body", b""))
        status = _status_code(response.get("status", response.get("status_code")))
        codings = _parse_codings(_header(handle, "content-encoding"))
        content_encoding = _display_codings(codings)
        content_type = _header(handle, "content-type")
        state.requests += 1
        state.last_seen = _now().isoformat(timespec="seconds")
        if not state.offered:
            state.offered = self._offer(state)

        result = {
            "site": site,
            "url": response.get("url"),
            "status": "ok",
            "ok": True,
            "failed": False,
            "text": None,
            "lossy_text": None,
            "raw": body,
            "content": None,
            "encoding": None,
            "charset_source": None,
            "content_encoding": content_encoding,
            "compressed": content_encoding,   # 兼容旧字段名
            "layers": [],
            "decompressed": True,
            "decompression": "identity",
            "negotiation": None,
            "failure": None,
            "reason": None,
            # 解压失败时原样留一份可重放的响应（只含 body/headers/status/url）
            "_response": {"body": body, "headers": dict(handle),
                          "status": status, "url": response.get("url")},
        }

        # 站点直接拒绝压缩（406/415/501）：先退回 identity，再谈解码。
        if status in (406, 415, 501):
            reason = "站点拒绝压缩请求 (HTTP %s)" % status
            self._note_failure(state, "rejected", reason,
                               content_encoding=content_encoding)
            if state.site is not None and not state.locks("accept_encoding"):
                self._fallback(state, reason)
            result.update(status="rejected", ok=False, failed=True,
                          decompressed=False, decompression="identity",
                          failure={"stage": "rejected", "reason": reason,
                                   "content_encoding": content_encoding})
            result["reason"] = reason
            return self._finish(state, result, was_probed)

        if not body:
            # 204/304 的空体是协议允许的正常结果，和"站点给了空页面"分开报。
            # 没有正文就无从判断协商结论，只报状态、不改记录。
            if status in (204, 304):
                result.update(status="no_content", ok=True,
                              decompression="identity",
                              failure=None, reason="HTTP %s 无响应体" % status)
                return self._finish(state, result, was_probed)
            reason = "响应体为空"
            self._note_failure(state, "empty", reason,
                               content_encoding=content_encoding)
            result.update(status="empty", ok=False, failed=True,
                          decompressed=False,
                          failure={"stage": "empty", "reason": reason,
                                   "content_encoding": content_encoding})
            result["reason"] = reason
            return self._finish(state, result, was_probed)

        # 多层 content-encoding 从后往前逐层解压。
        data = body
        applied = []
        for name in reversed(codings):
            codec, canonical = _codec(name)
            if codec is None:
                return self._decompress_failure(
                    state, result, "未知压缩方式 %r" % canonical,
                    canonical, was_probed)
            if not codec.available:
                return self._decompress_failure(
                    state, result,
                    "%s 解码器不可用（%s）" % (canonical, codec.detail),
                    canonical, was_probed)
            try:
                data = codec.decompress(data)
            except CodecError as exc:
                return self._decompress_failure(
                    state, result, "%s: %s" % (canonical, exc),
                    canonical, was_probed)
            except Exception as exc:  # noqa: BLE001 - 解压器什么都可能抛
                return self._decompress_failure(
                    state, result, "%s: %s" % (canonical, exc),
                    canonical, was_probed)
            applied.append(canonical)

        result["content"] = data
        result["layers"] = applied
        result["decompression"] = "ok" if applied else "identity"

        # 字符集：人工覆盖优先，否则按 BOM/头/meta/内容判断。
        if state.locks("charset") and state.charset:
            charset, source = state.charset, "override"
        else:
            charset, source = detect_charset(
                data, content_type,
                hinted=None if state.locks("charset") else state.charset)
            if source != "unknown":
                charset = _normalize_charset(charset) or charset

        try:
            text = data.decode(charset)
        except (UnicodeDecodeError, LookupError) as exc:
            reason = "%s: %s" % (charset, exc)
            self._note_failure(state, "decode", reason,
                               content_encoding=content_encoding)
            # 不把替换成问号的文本当作正文：text 保持 None，另给一份
            # lossy_text 仅供人眼排查，调用方看 ok/status 决定是否使用。
            lossy = None
            if _known_codec(charset):
                lossy = data.decode(charset, "replace")
            result.update(status="decode_failed", ok=False, failed=True,
                          encoding=charset, charset_source=source,
                          text=None, lossy_text=lossy,
                          content=data,
                          failure={"stage": "decode", "reason": reason,
                                   "content_encoding": content_encoding})
            result["reason"] = reason
            return self._finish(state, result, was_probed)

        result.update(text=_strip_bom(text), encoding=charset,
                      charset_source=source)

        # 记录最近一次协商结果；被人工锁定的字段保持覆盖值不变。
        if not state.locks("accept_encoding"):
            state.accept_encoding = content_encoding
            state.probed = True
            # 退回 identity 后，只有在真的拿到带压缩的响应时才算恢复；
            # 一直发 identity 也一直成功，说明回退仍然生效，原因要保留。
            if codings:
                state.fallback = False
                state.fallback_reason = None
        if not state.locks("charset"):
            state.charset = charset

        return self._finish(state, result, was_probed)

    def _finish(self, state, result, was_probed):
        state.last = _summarize(result)
        result["negotiation"] = self._negotiation(
            state, _source_for(state, was_probed))
        result.pop("_response", None)
        if state.site is not None:
            self._save()
        return result

    def _decompress_failure(self, state, result, reason, coding, was_probed):
        """解压失败：保留原始字节，明确标记，不当空页面。

        失败时 ``raw`` 保留站点发来的原始字节，``content``/``text`` 为
        None——调用方据此跳过该响应，而不是把二进制或空串当正文。
        """
        self._note_failure(state, "decompress", reason,
                           content_encoding=result["content_encoding"],
                           layer=coding)
        # 保留原始响应供排查，直到 clear_failed()。
        if state.site is not None:
            kept = self._failed_raw.setdefault(state.site, [])
            kept.append({
                "response": dict(result["_response"]),
                "time": state.last_seen,
                "reason": reason,
            })
            del kept[:-_MAX_KEPT_RESPONSES]

        if not state.locks("accept_encoding"):
            self._fallback(state, reason)

        result.update(status="decompress_failed", ok=False, failed=True,
                      decompressed=False, decompression="failed",
                      content=None, text=None,
                      failure={"stage": "decompress", "reason": reason,
                               "content_encoding": result["content_encoding"],
                               "layer": coding})
        result["reason"] = reason
        return self._finish(state, result, was_probed)

    def _note_failure(self, state, stage, reason, content_encoding=None,
                      layer=None):
        entry = {"stage": stage, "reason": reason,
                 "time": _now().isoformat(timespec="seconds")}
        if content_encoding is not None:
            entry["content_encoding"] = content_encoding
        if layer is not None:
            entry["layer"] = layer
        state.failures = (state.failures + [entry])[-_MAX_FAILURES:]
        state.last_seen = entry["time"]

    def _fallback(self, state, reason):
        """协商失败：退回不压缩，并记下原因。锁定后不自动改。"""
        state.accept_encoding = "identity"
        state.probed = True
        state.fallback = True
        state.fallback_reason = reason

    def _negotiation(self, state, source):
        return {
            "site": state.site,
            "accept_encoding": state.accept_encoding,
            "charset": state.charset,
            "probed": state.probed,
            "locked": state.locked,
            "locked_fields": list(state.locked_fields),
            "source": source,
            "offered": list(state.offered),
        }

    # -- 人工覆盖 / 解锁 -----------------------------------------------

    def override(self, site, accept_encoding=None, charset=None):
        """人工覆盖协商结果并锁定，之后自动协商不再改回。

        只锁定传进来的字段：只给 ``charset`` 时压缩方式仍然自动协商，
        反之亦然。两项都要锁时一起传。
        """
        if site is None:
            raise ValueError("override() 需要站点名")
        if accept_encoding is None and charset is None:
            raise ValueError(
                "override() 至少需要 accept_encoding 或 charset 之一")
        state = self._state(site)
        locked = [name for name in state.locked_fields]
        if accept_encoding is not None:
            state.accept_encoding = _normalize_accept(accept_encoding)
            if "accept_encoding" not in locked:
                locked.append("accept_encoding")
        if charset is not None:
            normalized = _normalize_charset(charset)
            if normalized is None:
                raise ValueError("未知字符集 %r" % (charset,))
            state.charset = normalized
            if "charset" not in locked:
                locked.append("charset")
        state.locked_fields = locked
        state.probed = True
        state.fallback = False
        state.fallback_reason = None
        state.last_seen = _now().isoformat(timespec="seconds")
        self._save()
        return self.record(site)

    def unlock(self, site):
        """站点改版后解锁：清掉锁定与结论，下次访问重新探测。"""
        if site is None:
            raise ValueError("unlock() 需要站点名")
        state = self._state(site)
        state.locked_fields = []
        state.probed = False
        state.accept_encoding = None
        state.charset = None
        state.fallback = False
        state.fallback_reason = None
        state.last_seen = _now().isoformat(timespec="seconds")
        self._save()
        return self.record(site)

    # -- 自检 ----------------------------------------------------------

    def selfcheck(self, site, probe_response=None):
        """新站点接入时跑一次协商自检；失败只影响这个站点。

        不传 ``probe_response`` 时只做本地回环：用本机编码器压一段样本、
        再用同一解码器解回来，确认声明出去的每种压缩方式都解得开；解不开
        的从本站点的探测列表里淘汰。传了探测响应则顺便验证一次真实解码，
        并按结果记住协商结论。
        """
        if site is None:
            raise ValueError("selfcheck() 需要站点名")
        state = self._state(site)
        report = {"site": site, "ok": True, "usable": True,
                  "offered": [], "checks": [], "selected": None,
                  "charset": None, "detail": None,
                  "time": _now().isoformat(timespec="seconds")}
        sample = "compressneg 协商自检 正文".encode("utf-8")

        offered = []
        for name in self._offer(state):
            codec = _CODECS[name]
            check = {"codec": name, "ok": None, "detail": None}
            if codec.encode is None:
                # 只有解码器没有编码器：无法本地回环，保留在探测列表里。
                check["detail"] = "缺少编码器，跳过本地回环"
                offered.append(name)
            else:
                try:
                    restored = codec.decompress(codec.encode(sample))
                    check["ok"] = restored == sample
                    if not check["ok"]:
                        check["detail"] = "本地回环结果不一致"
                except Exception as exc:  # noqa: BLE001
                    check["ok"] = False
                    check["detail"] = str(exc)
                if check["ok"] is False:
                    if name not in state.disabled:
                        state.disabled.append(name)
                else:
                    offered.append(name)
            report["checks"].append(check)

        report["offered"] = offered
        if not offered:
            report["usable"] = False
            report["ok"] = False
            report["detail"] = "本机没有任何可用的压缩解码器，退回 identity"
            if not state.locks("accept_encoding"):
                self._fallback(state, report["detail"])

        if probe_response is not None:
            result = self.decode(site, probe_response)
            report["decode"] = _summarize(result)
            if not result["ok"]:
                report["ok"] = False
                report["detail"] = result["reason"]
                report["selected"] = state.accept_encoding
                report["charset"] = state.charset
            else:
                report["selected"] = state.accept_encoding
                report["charset"] = result["encoding"]

        if any(check["ok"] is False for check in report["checks"]):
            report["ok"] = False
            if report["detail"] is None:
                bad = [check["codec"] for check in report["checks"]
                       if check["ok"] is False]
                report["detail"] = "本机解不开：%s" % ", ".join(bad)

        state.selfcheck = {"ok": report["ok"], "detail": report["detail"],
                           "offered": report["offered"],
                           "time": report["time"]}
        self._save()
        report["negotiation"] = self._negotiation(
            state, _source_for(state, state.probed))
        return report

    # -- 查询 ----------------------------------------------------------

    def record(self, site):
        """返回站点的协商记录（最近一次结果、失败原因、自检结论）。"""
        state = self._sites.get(site)
        if state is None:
            state = _new_state(site)
        return {
            "site": state.site,
            "accept_encoding": state.accept_encoding,
            "charset": state.charset,
            "probed": state.probed,
            "locked": state.locked,
            "locked_fields": list(state.locked_fields),
            "fallback": state.fallback,
            "fallback_reason": state.fallback_reason,
            "offered": list(state.offered),
            "disabled": list(state.disabled),
            "source": _source_for(state, state.probed),
            "failures": list(state.failures),
            "last": state.last,
            "selfcheck": state.selfcheck,
            "requests": state.requests,
            "last_seen": state.last_seen,
        }

    def records(self):
        """所有站点的协商记录，按站点名排序。"""
        return {name: self.record(name) for name in sorted(self._sites)}

    def failed_responses(self, site):
        """解压失败时保留的原始响应，供排查用。"""
        return [entry["response"] for entry in self._failed_raw.get(site, [])]

    def failed_details(self, site):
        """保留的原始响应连同时间和原因。"""
        return [dict(entry) for entry in self._failed_raw.get(site, [])]

    def clear_failed(self, site):
        """排查结束后清掉保留的原始响应。"""
        self._failed_raw.pop(site, None)


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------

def _split_args(site_or_response, response):
    """兼容 decode(site, response) 与 decode(response) 两种写法。"""
    if response is None:
        if isinstance(site_or_response, dict):
            return None, site_or_response
        raise TypeError("decode() 需要响应 dict，或 (site, response)")
    if isinstance(site_or_response, dict):
        # decode(response, site)
        return response, site_or_response
    return site_or_response, response


def _known_codec(charset):
    try:
        codecs.lookup(charset)
        return True
    except (LookupError, TypeError):
        return False


def _strip_bom(text):
    """解码后的正文去掉开头 BOM 字符，避免正文第一格是 \\ufeff。"""
    if text and text[0] == "﻿":
        return text[1:]
    return text


def _status_code(value):
    """把 status 统一成 int；没给或不是数字时返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_accept(value):
    """规范化人工指定的 accept-encoding，并拒绝本机解不开的方式。"""
    names = []
    for part in str(value).split(","):
        token = part.strip().lower()
        if not token:
            continue
        if token == "identity":
            names.append("identity")
            continue
        codec, canonical = _codec(token)
        if codec is None:
            raise ValueError("未知压缩方式 %r" % (token,))
        if not codec.available:
            raise ValueError("压缩方式 %r 本机不可用（%s）"
                             % (canonical, codec.detail))
        names.append(canonical)
    if not names:
        raise ValueError("accept-encoding 不能为空")
    # identity 只是兜底，和具体方式并列时没有意义。
    concrete = [name for name in names if name != "identity"]
    return ", ".join(concrete) if concrete else "identity"


def _summarize(result):
    """协商记录里保存的结果摘要，不含正文，便于落盘。"""
    failure = result.get("failure")
    return {
        "status": result.get("status"),
        "ok": result.get("ok"),
        "content_encoding": result.get("content_encoding"),
        "layers": list(result.get("layers") or []),
        "decompression": result.get("decompression"),
        "encoding": result.get("encoding"),
        "charset_source": result.get("charset_source"),
        # 拷贝一份，避免调用方改返回的 failure 时连带改掉记录里的摘要。
        "failure": dict(failure) if failure else None,
    }


default = Negotiator()


# ----------------------------------------------------------------------
# 模块级快捷函数（共用 default 实例）
# ----------------------------------------------------------------------

def headers(site):
    """``default.headers(site)`` 的快捷方式。"""
    return default.headers(site)


def decode(site_or_response, response=None):
    """``default.decode(...)`` 的快捷方式。"""
    return default.decode(site_or_response, response)


def selfcheck(site, probe_response=None):
    return default.selfcheck(site, probe_response)


def override(site, accept_encoding=None, charset=None):
    return default.override(site, accept_encoding=accept_encoding,
                            charset=charset)


def unlock(site):
    return default.unlock(site)


def record(site):
    return default.record(site)


def records():
    return default.records()


def failed_responses(site):
    return default.failed_responses(site)


def clear_failed(site):
    return default.clear_failed(site)
