"""带元数据、内容去重和校验的快照版本管理。"""
import fnmatch
import hashlib
import json
import os
import shutil
from datetime import timezone, datetime

TEMP_PATTERNS = ("*.tmp", "*.swp", "*.swo", "*.part", "*~", ".#*")


class SnapshotError(Exception):
    """快照缺失或内容校验失败。"""


def _is_temp(name):
    return any(fnmatch.fnmatch(name, pat) for pat in TEMP_PATTERNS)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


class Snapshot:
    def __init__(self, root, store):
        self.root = os.path.abspath(root)
        self.store = os.path.abspath(store)
        self.objects = os.path.join(self.store, "objects")
        self.manifests = os.path.join(self.store, "snapshots")
        self.refs_path = os.path.join(self.store, "refs.json")

    def create(self, tag):
        os.makedirs(self.objects, exist_ok=True)
        os.makedirs(self.manifests, exist_ok=True)
        refs = self._load_refs()
        old = self._read_manifest(tag)
        files = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            if os.path.abspath(dirpath) == self.store:
                dirnames[:] = []
                continue
            for name in sorted(filenames):
                if _is_temp(name):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, self.root)
                try:
                    with open(path, "rb") as fh:
                        data = fh.read()
                except OSError:
                    continue
                digest = _sha256(data)
                blob = os.path.join(self.objects, digest)
                if not os.path.exists(blob):
                    tmp_blob = blob + ".tmp"
                    with open(tmp_blob, "wb") as fh:
                        fh.write(data)
                    os.replace(tmp_blob, blob)
                files[rel] = {"size": len(data), "sha256": digest}
                refs[digest] = refs.get(digest, 0) + 1
        if old:
            self._release(old, refs)
        meta = {
            "tag": tag,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
        }
        with open(self._manifest_path(tag), "w") as fh:
            json.dump(meta, fh, indent=2, sort_keys=True)
        self._save_refs(refs)
        return meta

    def restore(self, tag):
        meta = self._read_manifest(tag)
        if meta is None:
            raise SnapshotError("快照不存在: %s" % tag)
        for rel, info in meta["files"].items():
            blob = os.path.join(self.objects, info["sha256"])
            if not os.path.exists(blob):
                raise SnapshotError("对象缺失，拒绝回滚: %s" % rel)
            with open(blob, "rb") as fh:
                data = fh.read()
            if _sha256(data) != info["sha256"] or len(data) != info["size"]:
                raise SnapshotError("校验和不符，拒绝回滚: %s" % rel)
        for name in os.listdir(self.root):
            path = os.path.join(self.root, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        count = 0
        for rel, info in meta["files"].items():
            dst = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(dst) or self.root, exist_ok=True)
            shutil.copyfile(os.path.join(self.objects, info["sha256"]), dst)
            count += 1
        return count

    def delete(self, tag):
        meta = self._read_manifest(tag)
        if meta is None:
            raise SnapshotError("快照不存在: %s" % tag)
        refs = self._load_refs()
        self._release(meta, refs)
        os.remove(self._manifest_path(tag))
        self._save_refs(refs)

    def _release(self, meta, refs):
        for info in meta["files"].values():
            digest = info["sha256"]
            refs[digest] = refs.get(digest, 1) - 1
            if refs[digest] <= 0:
                del refs[digest]
                blob = os.path.join(self.objects, digest)
                if os.path.exists(blob):
                    os.remove(blob)

    def _manifest_path(self, tag):
        return os.path.join(self.manifests, tag + ".json")

    def _read_manifest(self, tag):
        path = self._manifest_path(tag)
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            return json.load(fh)

    def _load_refs(self):
        if os.path.exists(self.refs_path):
            with open(self.refs_path) as fh:
                return json.load(fh)
        return {}

    def _save_refs(self, refs):
        with open(self.refs_path, "w") as fh:
            json.dump(refs, fh, indent=2, sort_keys=True)
