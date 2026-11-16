"""扫描结果的内存态：合并、使用痕迹、白名单与处置流。

scan() 不访问磁盘，所有状态都在进程内存里，reset() 可清空。
同一密钥按"内容"合并成一条，位置全部挂到 locations 下。
"""

import hashlib
import re
from datetime import datetime, timezone

from .detector import LOW, MEDIUM, HIGH, scan_text

# 处置状态。
OPEN = "open"          # 待处理
IN_USE = "in_use"      # 仍在使用，单独标出，禁止直接删
RESOLVED = "resolved"  # 已确认处理
WHITELISTED = "whitelisted"

_RANK = {LOW: 1, MEDIUM: 2, HIGH: 3}

# 定义形态：值紧跟在 标识符 = / 标识符: 之后（允许引号/空白）。
_DEF_RE = re.compile(r"[A-Za-z_][\w.\-]*\s*[:=]\s*['\"]?$")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _entry_id(value):
    return "key_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _mask(value):
    """报告里默认打码，避免二次泄露。"""
    if len(value) <= 8:
        return value[0] + "***"
    return value[:4] + "***" + value[-4:]


def _is_definition(line, value):
    """这一行是在"定义/赋值"密钥，还是在"调用"它。

    取该次出现之前的行首片段，去掉紧邻的引号后若以 key= / key: 收尾，
    视为定义；其它（函数参数、比较、裸 token 等）视为调用。
    """
    head = line.split(value, 1)[0].rstrip()
    head = head.rstrip("'\"")
    return bool(_DEF_RE.search(head))


class SecretStore:
    def __init__(self):
        self.reset()

    def reset(self):
        self._entries = {}          # entry_id -> merged entry
        self._whitelist = set()     # 精确值
        self._whitelist_patterns = []  # 正则 pattern 对象
        self._repo_files = {}       # repo -> 文件数

    # ------------------------------------------------------------------ #
    # 白名单
    # ------------------------------------------------------------------ #
    def whitelist(self, value=None, pattern=None):
        """把误报加入白名单。

        value：按密钥内容精确忽略；pattern：正则，匹配内容或命中行。
        已存在且处于 open 的同值条目会立刻转成 whitelisted。
        """
        if value is None and pattern is None:
            raise ValueError("whitelist needs value or pattern")
        if value is not None:
            self._whitelist.add(value)
        if pattern is not None:
            self._whitelist_patterns.append(re.compile(pattern))
        for entry in self._entries.values():
            if entry["status"] in (OPEN, IN_USE) and self._is_whitelisted(
                entry["value"], entry.get("_line_sample", "")
            ):
                entry["status"] = WHITELISTED
                entry["updated_at"] = _now()
        return {"whitelisted_values": len(self._whitelist),
                "whitelisted_patterns": len(self._whitelist_patterns)}

    def _is_whitelisted(self, value, line_sample=""):
        if value in self._whitelist:
            return True
        for pat in self._whitelist_patterns:
            if pat.search(value) or (line_sample and pat.search(line_sample)):
                return True
        return False

    # ------------------------------------------------------------------ #
    # 处置流
    # ------------------------------------------------------------------ #
    def triage(self, entry_id, handler, method, note=""):
        """记录确认处理：处理人 + 方式（rotated/revoked/removed/...）。"""
        entry = self._entries.get(entry_id)
        if entry is None:
            raise KeyError("unknown entry_id: %s" % entry_id)
        if not handler or not method:
            raise ValueError("handler and method are required")
        entry["handler"] = handler
        entry["method"] = method
        entry["note"] = note
        entry["status"] = RESOLVED
        entry["updated_at"] = _now()
        return entry

    # ------------------------------------------------------------------ #
    # 扫描与合并
    # ------------------------------------------------------------------ #
    def scan(self, paths):
        if not isinstance(paths, dict):
            raise ValueError("paths must map repo/file to content")
        repos = {}
        for name, text in paths.items():
            if not isinstance(name, str) or not name:
                raise ValueError("file name must be a non empty str")
            if not isinstance(text, str):
                raise ValueError("file content must be str")
            repo = name.split("/", 1)[0]
            self._repo_files[repo] = self._repo_files.get(repo, 0) + 1
            repos[repo] = repos.get(repo, 0) + 1

        # 第一遍：形态/上下文检测，先把本批所有密钥内容确定下来。
        first_pass = []
        for name, text in paths.items():
            for hit in scan_text(name, text):
                line_sample = text.splitlines()[hit["line"] - 1]
                if self._is_whitelisted(hit["value"], line_sample):
                    continue
                first_pass.append((name, text, hit, line_sample))
        known = {item[2]["value"] for item in first_pass}

        # 第二遍：带着已知密钥再扫一次，补抓裸值引用（使用痕迹）。
        seen = {(n, h["line"], h["value"]) for n, _t, h, _s in first_pass}
        for name, text in paths.items():
            for hit in scan_text(name, text, known_values=known):
                if (hit["file"], hit["line"], hit["value"]) in seen:
                    continue
                line_sample = text.splitlines()[hit["line"] - 1]
                if self._is_whitelisted(hit["value"], line_sample):
                    continue
                seen.add((hit["file"], hit["line"], hit["value"]))
                first_pass.append((name, text, hit, line_sample))

        for name, text, hit, line_sample in first_pass:
            repo = name.split("/", 1)[0]
            self._merge(repo, hit, line_sample)

        self._refresh_status()
        return {
            "entries": self.list_entries(),
            "files": len(paths),
            "repos": repos,
        }

    def _merge(self, repo, hit, line_sample):
        value = hit["value"]
        entry_id = _entry_id(value)
        location = {
            "repo": repo,
            "file": hit["file"],
            "line": hit["line"],
            "kind": hit["kind"],          # code / config / build
            "snippet": line_sample.strip()[:200],
        }
        # 使用痕迹：定义 vs 调用。
        usage = {
            "repo": repo,
            "file": hit["file"],
            "line": hit["line"],
            "kind": "definition" if _is_definition(line_sample, value) else "call",
        }

        entry = self._entries.get(entry_id)
        if entry is None:
            entry = {
                "id": entry_id,
                "value": value,
                "masked": _mask(value),
                "type": hit["type"],
                "confidence": hit["confidence"],
                "context": hit["context"],
                "repos": [repo],
                "locations": [location],
                "usage": [usage],
                "in_use": False,
                "status": OPEN,
                "handler": None,
                "method": None,
                "note": "",
                "created_at": _now(),
                "updated_at": _now(),
                "_line_sample": line_sample,
            }
            self._entries[entry_id] = entry
            return

        # 合并：位置去重，仓库去重，可信程度取最高。
        if _RANK[hit["confidence"]] > _RANK[entry["confidence"]]:
            entry["confidence"] = hit["confidence"]
            entry["type"] = hit["type"]
        if repo not in entry["repos"]:
            entry["repos"].append(repo)
        loc_key = (location["file"], location["line"])
        if loc_key not in {(l["file"], l["line"]) for l in entry["locations"]}:
            entry["locations"].append(location)
        use_key = (usage["file"], usage["line"])
        if use_key not in {(u["file"], u["line"]) for u in entry["usage"]}:
            entry["usage"].append(usage)
        entry["updated_at"] = _now()

    def _refresh_status(self):
        """根据使用痕迹刷新 in_use；已处置/白名单的不动。"""
        for entry in self._entries.values():
            if entry["status"] in (RESOLVED, WHITELISTED):
                continue
            has_call = any(u["kind"] == "call" for u in entry["usage"])
            # 同一密钥在多个文件出现：除定义点外的引用都算仍在使用。
            multi_ref = len({(u["file"], u["line"]) for u in entry["usage"]}) > 1
            entry["in_use"] = has_call or multi_ref
            entry["status"] = IN_USE if entry["in_use"] else OPEN

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def list_entries(self):
        """对外视图：去掉内部字段，按可信程度 + 出现位置排序。"""
        result = []
        for entry in self._entries.values():
            view = {k: v for k, v in entry.items() if not k.startswith("_")}
            view["locations"] = [dict(loc) for loc in entry["locations"]]
            view["usage"] = [dict(u) for u in entry["usage"]]
            result.append(view)
        result.sort(
            key=lambda e: (-_RANK[e["confidence"]], -len(e["locations"]), e["id"])
        )
        return result

    def report(self):
        entries = self.list_entries()
        by_repo = {}
        for entry in entries:
            for loc in entry["locations"]:
                bucket = by_repo.setdefault(
                    loc["repo"],
                    {"total": 0, "open": 0, "in_use": 0, "resolved": 0,
                     "whitelisted": 0, "entries": []},
                )
                if entry["id"] not in bucket["entries"]:
                    bucket["entries"].append(entry["id"])
                    bucket["total"] += 1
                    bucket[entry["status"]] = bucket.get(entry["status"], 0) + 1

        status_count = {OPEN: 0, IN_USE: 0, RESOLVED: 0, WHITELISTED: 0}
        for entry in entries:
            status_count[entry["status"]] = status_count.get(entry["status"], 0) + 1

        # 同一密钥出现在多处（含跨仓库）即记为重复暴露。
        duplicates = sum(1 for e in entries if len(e["locations"]) > 1)

        return {
            "total": len(entries),
            "open": status_count[OPEN],
            "in_use": status_count[IN_USE],
            "resolved": status_count[RESOLVED],
            "whitelisted": status_count[WHITELISTED],
            "duplicates": duplicates,
            "repos": {repo: data["total"] for repo, data in sorted(by_repo.items())},
            "repo_groups": by_repo,
            "entries": entries,
        }
