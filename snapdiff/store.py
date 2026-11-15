"""快照版本存储：zlib 压缩落盘，按站点/版本/时间查询，按保留期清理。"""
import json
import os
import re
import time
import zlib

from .snap import mark_false_positive

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _write_z(path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(zlib.compress(data, 9))


def _read_z(path):
    with open(path, "rb") as fh:
        return json.loads(zlib.decompress(fh.read()).decode("utf-8"))


class SnapshotStore:
    """按站点组织：vNNNNNN.json.z 存快照，changes/ 存比对记录，marks.json 存误报样本。"""

    def __init__(self, root, retention_days=30):
        self.root = root
        self.retention_days = retention_days
        os.makedirs(root, exist_ok=True)
        self._load_marks()

    def _site_dir(self, site):
        return os.path.join(self.root, _SAFE_RE.sub("_", site or "_default"))

    def _index_path(self, site):
        return os.path.join(self._site_dir(site), "index.json")

    def _index(self, site):
        path = self._index_path(site)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        return {"versions": []}

    def _save_index(self, site, index):
        with open(self._index_path(site), "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False)

    def _snap_path(self, site, version):
        return os.path.join(self._site_dir(site), "v%06d.json.z" % version)

    def save(self, snap):
        """持久化快照（压缩存储），返回分配的递增版本号。"""
        site = snap.get("site") or ""
        os.makedirs(self._site_dir(site), exist_ok=True)
        index = self._index(site)
        version = index["versions"][-1]["version"] + 1 if index["versions"] else 1
        payload = dict(snap, version=version)
        _write_z(self._snap_path(site, version), payload)
        index["versions"].append({"version": version, "ts": payload.get("ts", time.time())})
        self._save_index(site, index)
        return version

    def get(self, site, version):
        """按版本取快照，不存在返回 None。"""
        path = self._snap_path(site, version)
        return _read_z(path) if os.path.exists(path) else None

    def get_at(self, site, ts):
        """取不晚于 ts 的最新版本快照，没有则返回 None。"""
        candidates = [v for v in self._index(site)["versions"] if v["ts"] <= ts]
        if not candidates:
            return None
        return self.get(site, candidates[-1]["version"])

    def versions(self, site):
        """站点全部版本号列表（升序）。"""
        return [v["version"] for v in self._index(site)["versions"]]

    def record_diff(self, site, record):
        """比对结果落库，保留块级样本供运营复核。"""
        changes_dir = os.path.join(self._site_dir(site), "changes")
        os.makedirs(changes_dir, exist_ok=True)
        ts_ms = int(record.get("ts", time.time()) * 1000)
        seq = len([n for n in os.listdir(changes_dir) if n.endswith(".json.z")])
        _write_z(os.path.join(changes_dir, "%013d_%03d.json.z" % (ts_ms, seq)), record)

    def query_changes(self, site, start=None, end=None):
        """按站点和时间区间 [start, end] 查询变更记录，按时间升序。"""
        changes_dir = os.path.join(self._site_dir(site), "changes")
        if not os.path.isdir(changes_dir):
            return []
        records = []
        for name in sorted(os.listdir(changes_dir)):
            if not name.endswith(".json.z"):
                continue
            record = _read_z(os.path.join(changes_dir, name))
            ts = record.get("ts", 0)
            if start is not None and ts < start:
                continue
            if end is not None and ts > end:
                continue
            records.append(record)
        return records

    def mark_false_positive(self, site, before_fingerprint, after_fingerprint="", note=""):
        """标记误报样本：登记到全局抑制表并按站点持久化。"""
        mark_false_positive(before_fingerprint, after_fingerprint)
        path = os.path.join(self._site_dir(site), "marks.json")
        os.makedirs(self._site_dir(site), exist_ok=True)
        marks = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                marks = json.load(fh)
        marks.append({"before": before_fingerprint, "after": after_fingerprint,
                      "note": note, "ts": time.time()})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(marks, fh, ensure_ascii=False)

    def _load_marks(self):
        if not os.path.isdir(self.root):
            return
        for site_dir in os.listdir(self.root):
            path = os.path.join(self.root, site_dir, "marks.json")
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as fh:
                for mark in json.load(fh):
                    mark_false_positive(mark.get("before", ""), mark.get("after", ""))

    def cleanup(self, now=None):
        """按保留期清理过期快照和变更记录；每站点至少保留最新一版。

        返回删除的快照版本数。
        """
        now = time.time() if now is None else now
        cutoff = now - self.retention_days * 86400
        removed = 0
        for site_dir in os.listdir(self.root):
            full = os.path.join(self.root, site_dir)
            if not os.path.isdir(full):
                continue
            index = self._index(site_dir)
            versions = index["versions"]
            if not versions:
                continue
            keep, drop = [], []
            for entry in versions:
                if entry["ts"] < cutoff:
                    drop.append(entry)
                else:
                    keep.append(entry)
            # 至少保留最新一版作为比对基线
            if not keep:
                keep.append(drop.pop())
            for entry in drop:
                path = self._snap_path(site_dir, entry["version"])
                if os.path.exists(path):
                    os.remove(path)
                    removed += 1
            if drop:
                self._save_index(site_dir, {"versions": keep})
            changes_dir = os.path.join(full, "changes")
            if os.path.isdir(changes_dir):
                for name in os.listdir(changes_dir):
                    if name.endswith(".json.z") and int(name.split("_")[0]) < cutoff * 1000:
                        os.remove(os.path.join(changes_dir, name))
        return removed
