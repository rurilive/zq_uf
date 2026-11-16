"""问题条目的存储。

默认是进程内内存存储，传入路径则持久化到 JSON 文件，跨进程保留条目
状态——重新跑一晚上模糊测试后，昨天已关闭的条目仍然认得。

只存 JSON 原生类型（list/dict/str/int/bool/None），归一化指纹里的
tuple 在 triage 层写入前转成 list。
"""

import json
import os


class IssueStore:
    def __init__(self, path=None):
        self.path = path
        self.issues = []
        # sample_id -> 崩溃原始记录与归一化指纹，拆分条目时需要回查
        self.records = {}
        self._counter = 0
        self._sample_index = {}
        if path:
            self._load()
        self._reindex()

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.issues = data.get("issues", [])
        self.records = data.get("records", {})
        self._counter = data.get("counter", 0)

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                {"counter": self._counter, "issues": self.issues,
                 "records": self.records},
                fh,
                ensure_ascii=False,
                indent=2,
            )
        os.replace(tmp, self.path)

    def _reindex(self):
        self._sample_index = {}
        for issue in self.issues:
            for sample_id in issue["samples"]:
                self._sample_index[sample_id] = issue["issue_id"]

    def next_id(self):
        self._counter += 1
        return "ISSUE-%d" % self._counter

    def get(self, issue_id):
        for issue in self.issues:
            if issue["issue_id"] == issue_id:
                return issue
        return None

    def find_by_sample(self, sample_id):
        issue_id = self._sample_index.get(sample_id)
        return self.get(issue_id) if issue_id else None

    def candidates_for(self, compatible_keys):
        """按兼容 cause_key 集合（含人工合并带入的 extra_keys）找候选条目。"""
        key_set = {tuple(key) for key in compatible_keys}
        result = []
        for issue in self.issues:
            issue_key = tuple(issue["cause_key"])
            if issue_key in key_set:
                result.append(issue)
                continue
            for extra in issue.get("extra_keys", []):
                if tuple(extra) in key_set:
                    result.append(issue)
                    break
        return result

    def add(self, issue):
        self.issues.append(issue)
        for sample_id in issue["samples"]:
            self._sample_index[sample_id] = issue["issue_id"]

    def index_sample(self, sample_id, issue_id):
        self._sample_index[sample_id] = issue_id

    def remove(self, issue_id):
        issue = self.get(issue_id)
        if issue is None:
            return None
        for sample_id in issue["samples"]:
            self._sample_index.pop(sample_id, None)
        self.issues = [item for item in self.issues if item["issue_id"] != issue_id]
        return issue

    def reset(self):
        self.issues = []
        self.records = {}
        self._counter = 0
        self._sample_index = {}
        if self.path and os.path.exists(self.path):
            os.remove(self.path)
