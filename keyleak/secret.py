"""密钥扫描，当前按关键字找赋值行。

paths 是 仓库/文件 到文件内容的映射，扫描过程不访问磁盘。
"""

KEYWORDS = ("password", "passwd", "secret", "token", "api_key")

_entries = []


def _scan_text(repo, name, text):
    """一行里有密钥字样并且带等号，就把等号右边当成密钥。"""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        for word in KEYWORDS:
            if word in lowered and "=" in line:
                value = line.split("=", 1)[1].strip().strip("'\"")
                found.append(
                    {
                        "repo": repo,
                        "file": name,
                        "line": number,
                        "keyword": word,
                        "value": value,
                        "confidence": "low",
                        "usage": [],
                        "status": "open",
                    }
                )
                break
    return found


def scan(paths):
    """逐个文件找关键字，同一份密钥出现几次就记几条。"""
    global _entries
    if not isinstance(paths, dict):
        raise ValueError("paths must map repo/file to content")
    _entries = []
    repos = {}
    for name, text in paths.items():
        if not isinstance(name, str) or not name:
            raise ValueError("file name must be a non empty str")
        if not isinstance(text, str):
            raise ValueError("file content must be str")
        repo = name.split("/", 1)[0]
        repos[repo] = repos.get(repo, 0) + 1
        _entries.extend(_scan_text(repo, name, text))
    return {
        "entries": _entries,
        "files": len(paths),
        "repos": repos,
    }


def report():
    """按仓库数条目，不合并同值密钥。"""
    repos = {}
    for entry in _entries:
        repos[entry["repo"]] = repos.get(entry["repo"], 0) + 1
    return {
        "total": len(_entries),
        "open": len([e for e in _entries if e["status"] == "open"]),
        "repos": repos,
        "duplicates": 0,
    }
