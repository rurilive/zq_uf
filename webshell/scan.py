"""webshell 扫描，当前只按关键字在整份文件里找。

输入不落盘，path 是文件名到文件内容的映射，调用方自己决定读哪些文件。
"""

KEYWORDS = ("eval", "assert", "system", "base64_decode")

_hits = []
_whitelist = set()


def _check_files(path):
    if not isinstance(path, dict):
        raise ValueError("path must map file names to content")
    for name, text in path.items():
        if not isinstance(name, str) or not name:
            raise ValueError("file name must be a non empty str")
        if not isinstance(text, str):
            raise ValueError("file content must be str")
    return path


def scan(path):
    """逐行找关键字，每命中一次记一条，同一文件不合并。"""
    global _hits
    files = _check_files(path)
    _hits = []
    for name, text in files.items():
        if name in _whitelist:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            for word in KEYWORDS:
                if word in lowered:
                    _hits.append({"file": name, "keyword": word})
                    break
    return {
        "scanned": len(files),
        "hits": len(_hits),
        "files": sorted({hit["file"] for hit in _hits}),
    }


def report():
    """按文件汇总命中次数，不带行号和片段。"""
    counted = {}
    for hit in _hits:
        counted[hit["file"]] = counted.get(hit["file"], 0) + 1
    return {
        "total": len(_hits),
        "files": sorted(counted),
        "counts": counted,
        "whitelist": len(_whitelist),
    }


def whitelist(name):
    """把一个文件加入跳过名单。"""
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non empty str")
    _whitelist.add(name)
    return {"path": name, "whitelist": len(_whitelist)}
