"""版本存储后端。

元数据（版本链、提交人、校验和、结构变更）与数据（行快照）分开存储，
提供两种后端：

* ``MemoryStorage``：进程内字典存储，默认使用；
* ``FileStorage``：目录存储，布局为 ``metadata/``、``data/``、``archive/``。
"""

import json
import os


def _clone(obj):
    """JSON 往返深拷贝，同时保证调用方后续改动不影响已存版本。"""
    return json.loads(json.dumps(obj, ensure_ascii=False))


def _safe_name(name):
    if not isinstance(name, str) or not name:
        raise ValueError("表名必须是非空字符串: %r" % (name,))
    if "/" in name or "\\" in name or os.sep in name or name in (".", ".."):
        raise ValueError("非法表名: %r" % (name,))
    return name


class MemoryStorage:
    """内存后端，元数据与数据使用独立字典，归档区同样分开。"""

    def __init__(self):
        self.meta = {}
        self.data = {}
        self.archived_meta = {}
        self.archived_data = {}
        self.state = {"heads": {}, "counters": {}, "last_table": None}

    def save_state(self):
        """内存后端无需落盘。"""

    # -- 元数据 -------------------------------------------------------
    def write_meta(self, table, version, meta):
        self.meta[(table, version)] = _clone(meta)

    def read_meta(self, table, version):
        return _clone(self.meta[(table, version)])

    def list_versions(self, table):
        return sorted(v for (t, v) in self.meta if t == table)

    # -- 数据 ---------------------------------------------------------
    def write_data(self, table, version, payload):
        self.data[(table, version)] = _clone(payload)

    def read_data(self, table, version):
        return _clone(self.data[(table, version)])

    # -- 归档 ---------------------------------------------------------
    def archive(self, table, version):
        self.archived_meta[(table, version)] = self.meta.pop((table, version))
        self.archived_data[(table, version)] = self.data.pop((table, version))

    def list_archived(self, table=None):
        if table is None:
            return sorted(self.archived_meta.keys(), key=lambda kv: (kv[0], kv[1]))
        return sorted(v for (t, v) in self.archived_meta if t == table)

    def is_archived(self, table, version):
        return (table, version) in self.archived_meta


class FileStorage:
    """文件后端。

    目录布局::

        <root>/state.json
        <root>/metadata/<table>/v000001.json   # 版本元数据
        <root>/data/<table>/v000001.json       # 行快照数据
        <root>/archive/metadata/<table>/...    # 归档元数据
        <root>/archive/data/<table>/...        # 归档数据
    """

    def __init__(self, root):
        self.root = root
        self.meta_dir = os.path.join(root, "metadata")
        self.data_dir = os.path.join(root, "data")
        self.archive_root = os.path.join(root, "archive")
        self.state_path = os.path.join(root, "state.json")
        os.makedirs(root, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"heads": {}, "counters": {}, "last_table": None}

    def save_state(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self.state_path)

    def _dir(self, base, table):
        path = os.path.join(base, _safe_name(table))
        os.makedirs(path, exist_ok=True)
        return path

    def _path(self, kind, table, version, archived):
        if kind == "meta":
            base = os.path.join(self.archive_root, "metadata") if archived else self.meta_dir
        else:
            base = os.path.join(self.archive_root, "data") if archived else self.data_dir
        return os.path.join(self._dir(base, table), "v%06d.json" % version)

    @staticmethod
    def _version_from_name(filename):
        stem, ext = os.path.splitext(filename)
        if ext != ".json" or not stem.startswith("v"):
            return None
        try:
            return int(stem[1:])
        except ValueError:
            return None

    def _list(self, base, table):
        directory = os.path.join(base, _safe_name(table))
        if not os.path.isdir(directory):
            return []
        versions = []
        for filename in os.listdir(directory):
            v = self._version_from_name(filename)
            if v is not None:
                versions.append(v)
        return sorted(versions)

    # -- 元数据 -------------------------------------------------------
    def write_meta(self, table, version, meta):
        path = self._path("meta", table, version, archived=False)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, sort_keys=True)

    def read_meta(self, table, version):
        archived = self.is_archived(table, version)
        path = self._path("meta", table, version, archived)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_versions(self, table):
        return self._list(self.meta_dir, table)

    # -- 数据 ---------------------------------------------------------
    def write_data(self, table, version, payload):
        path = self._path("data", table, version, archived=False)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)

    def read_data(self, table, version):
        archived = self.is_archived(table, version)
        path = self._path("data", table, version, archived)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # -- 归档 ---------------------------------------------------------
    def archive(self, table, version):
        for kind in ("meta", "data"):
            src = self._path(kind, table, version, archived=False)
            dst = self._path(kind, table, version, archived=True)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)

    def list_archived(self, table=None):
        if table is None:
            result = []
            base = os.path.join(self.archive_root, "metadata")
            if not os.path.isdir(base):
                return result
            for t in os.listdir(base):
                for v in self._list(base, t):
                    result.append((t, v))
            return sorted(result, key=lambda kv: (kv[0], kv[1]))
        return self._list(os.path.join(self.archive_root, "metadata"), table)

    def is_archived(self, table, version):
        return os.path.exists(self._path("meta", table, version, archived=True))
