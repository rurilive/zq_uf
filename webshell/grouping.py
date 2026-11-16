"""把同一目录下的相似文件合并成一组。

两个文件满足任一条件即视为相似（并查集传递合并）：
1. 规范化后的内容相似度 >= 阈值（注释/字符串/变量名/数字全部归一化）；
2. 还原出来的载荷（折叠字符串、chr 链、解码结果）完全一致。
"""

import os
import re

SIMILARITY_THRESHOLD = 0.8

_VAR = re.compile(r"\$[A-Za-z_]\w*")
_STRING = re.compile(r"(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")")
_NUMBER = re.compile(r"\b\d+\b")
_WS = re.compile(r"\s+")
_COMMENT = re.compile(r"(?m)#.*$|//.*$|/\*.*?\*/", re.DOTALL)


def normalize(text):
    """把变量名、字符串、数字统一替换成占位符，只保留结构骨架。"""
    text = _COMMENT.sub(" ", text)
    text = _STRING.sub("S", text)
    text = _VAR.sub("V", text)
    text = _NUMBER.sub("N", text)
    return _WS.sub(" ", text).strip()


def _similarity(a, b):
    """difflib 序列相似度，长度差异过大的骨架直接判 0。"""
    if not a or not b:
        return 0.0
    short, long_ = sorted((a, b), key=len)
    if len(short) / len(long_) < 0.5:
        return 0.0
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


class _UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a


def group_files(findings, source_by_name):
    """按目录对命中文件做相似度聚类，只返回成员数 >= 2 的组。

    findings: scan 产出的 finding 列表；source_by_name: 文件名 -> 原文。
    返回 [{id, directory, files, reason}]，并就地给每个 finding 写 group_id。
    """
    by_directory = {}
    for finding in findings:
        directory = os.path.dirname(finding["file"]) or "."
        by_directory.setdefault(directory, []).append(finding["file"])

    groups = []
    serial = 0
    for directory, names in sorted(by_directory.items()):
        names = sorted(set(names))
        if len(names) < 2:
            continue

        uf = _UnionFind(names)
        normalized = {name: normalize(source_by_name[name]) for name in names}
        payloads = {}
        for finding in findings:
            for record in finding.get("restoration", []):
                payloads.setdefault(record["to"], set()).add(finding["file"])

        for i, left in enumerate(names):
            for right in names[i + 1 :]:
                if _similarity(normalized[left], normalized[right]) >= SIMILARITY_THRESHOLD:
                    uf.union(left, right)
        for members in payloads.values():
            members = [name for name in members if name in names]
            for other in members[1:]:
                uf.union(members[0], other)

        clusters = {}
        for name in names:
            clusters.setdefault(uf.find(name), []).append(name)
        for members in clusters.values():
            if len(members) < 2:
                continue
            serial += 1
            group_id = f"group-{serial:03d}"
            groups.append(
                {
                    "id": group_id,
                    "directory": directory,
                    "files": sorted(members),
                    "reason": "normalized similarity >= %.2f or identical restored payload"
                    % SIMILARITY_THRESHOLD,
                }
            )

    group_index = {
        name: group["id"] for group in groups for name in group["files"]
    }
    for finding in findings:
        finding["group_id"] = group_index.get(finding["file"])
    return groups
