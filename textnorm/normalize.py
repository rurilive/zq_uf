"""编码和换行规范化：识别 BOM 与真实编码，统一输出 UTF-8 无 BOM、LF 换行。

流式处理，内存占用与文件大小无关；保留原始行号映射，无法解码的行保留并标记。
"""

import codecs

_CHUNK_SIZE = 65536

# 注意顺序：UTF-32 的 BOM 以 UTF-16 的 BOM 开头，必须先匹配长的。
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

# 调用方编码失败后的回退顺序：gb18030 是 GBK 的超集，latin-1 不丢字节。
_FALLBACKS = ("gb18030", "latin-1")


def _sniff(sample, encoding):
    """返回 (实际编码, BOM 长度, 是否带 BOM)。"""
    for bom, name in _BOMS:
        if sample.startswith(bom):
            return name, len(bom), True
    candidates = [encoding] + [c for c in _FALLBACKS if c != encoding]
    for candidate in candidates:
        try:
            decoder = codecs.getincrementaldecoder(candidate)(errors="strict")
        except LookupError:
            continue
        try:
            # final=False：样本末尾截断的不完整序列会缓冲，不误判。
            decoder.decode(sample, final=False)
        except UnicodeDecodeError:
            continue
        return candidate, 0, False
    return "latin-1", 0, False


def normalize(path, encoding="utf-8", keep_text=True):
    """规范化 path 指向的文件，结果写入 path + ".norm"。

    encoding 是调用方给的预期编码，仅作嗅探提示；BOM 优先，
    其后依次尝试该编码、gb18030、latin-1。

    返回字典：
        text      规范化后的全文（keep_text=False 时为 None，大文件省内存）
        out       输出文件路径
        lines     输出中 LF 的数量
        encoding  实际使用的解码编码
        had_bom   输入是否带 BOM
        line_map  输出行号（下标+1）-> 原始文件 1 起始行号
        bad_lines 含无法解码字节的原始行号（行内以 U+FFFD 标记保留）
    """
    out = path + ".norm"
    parts = [] if keep_text else None
    line_map = []
    bad_lines = []
    lines = 0
    line_no = 0
    carry = ""    # 跨块的孤立 "\r"，可能是 "\r\n" 被切块拆开
    partial = ""  # 尚未遇到换行符的当前行内容

    with open(path, "rb") as handle:
        sample = handle.read(_CHUNK_SIZE)
        detected, bom_len, had_bom = _sniff(sample, encoding)
        handle.seek(bom_len)
        decoder = codecs.getincrementaldecoder(detected)(errors="replace")

        with open(out, "w", encoding="utf-8", newline="") as writer:
            while True:
                chunk = handle.read(_CHUNK_SIZE)
                final = not chunk
                text = decoder.decode(chunk, final=final)
                if carry:
                    text = carry + text
                    carry = ""
                if not final and text.endswith("\r"):
                    carry = "\r"
                    text = text[:-1]
                if text:
                    segment = text.replace("\r\n", "\n").replace("\r", "\n")
                    writer.write(segment)
                    if parts is not None:
                        parts.append(segment)
                    lines += segment.count("\n")
                    buffer = partial + segment
                    *done, partial = buffer.split("\n")
                    for item in done:
                        line_no += 1
                        line_map.append(line_no)
                        if "\ufffd" in item:
                            bad_lines.append(line_no)
                if final:
                    break

    if partial:
        line_no += 1
        line_map.append(line_no)
        if "\ufffd" in partial:
            bad_lines.append(line_no)

    return {
        "text": "".join(parts) if parts is not None else None,
        "out": out,
        "lines": lines,
        "encoding": detected,
        "had_bom": had_bom,
        "line_map": line_map,
        "bad_lines": bad_lines,
    }
