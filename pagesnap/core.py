"""页面快照：内容寻址去重存储、站点/时间索引、结构比对。"""
import datetime
import gzip
import hashlib
import json
import os
import time
from difflib import SequenceMatcher
from html.parser import HTMLParser

OBJECTS_DIR = "objects"
INDEX_DIR = "index"
SECONDS_PER_DAY = 86400


class _StructureParser(HTMLParser):
    """从 HTML 提取关键结构（标签序列）。"""

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)


def _coerce_time(value):
    if value is None:
        return time.time()
    if isinstance(value, str):
        return datetime.datetime.fromisoformat(value).timestamp()
    return float(value)


def _structure_of(page):
    if "structure" in page:
        return list(page["structure"])
    html = page.get("html")
    if html:
        parser = _StructureParser()
        parser.feed(html)
        return parser.tags
    return []


def _digest(text, structure):
    payload = json.dumps({"text": text, "structure": structure}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _object_paths(directory, digest, compress=False):
    base = os.path.join(directory, OBJECTS_DIR, digest)
    text_path = base + (".txt.gz" if compress else ".txt")
    return text_path, base + ".meta.json"


def _stamp(moment):
    point = datetime.datetime.fromtimestamp(moment, tz=datetime.timezone.utc)
    return point.strftime("%Y%m%dT%H%M%S%f")


def _read_text(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _meta_path_for(text_path):
    base = text_path[:-3] if text_path.endswith(".gz") else text_path
    if base.endswith(".txt"):
        base = base[:-4]
    return base + ".meta.json"


def _load_structure(text_path):
    meta_path = _meta_path_for(text_path)
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as handle:
            return json.load(handle).get("structure", [])
    return []


def save(page, directory, retention_days=None, compress=False):
    """保存页面快照，返回正文文件路径。相同内容只写一份。"""
    text = page.get("text", "")
    structure = _structure_of(page)
    site = page.get("site", "default")
    moment = _coerce_time(page.get("time"))
    digest = _digest(text, structure)

    text_path, meta_path = _object_paths(directory, digest, compress)
    os.makedirs(os.path.dirname(text_path), exist_ok=True)
    if not os.path.exists(text_path):
        if compress:
            with gzip.open(text_path, "wt", encoding="utf-8") as handle:
                handle.write(text)
        else:
            with open(text_path, "w", encoding="utf-8") as handle:
                handle.write(text)
    if not os.path.exists(meta_path):
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump({"hash": digest, "structure": structure}, handle)

    entry_dir = os.path.join(directory, INDEX_DIR, site, page["id"])
    os.makedirs(entry_dir, exist_ok=True)
    entry = {
        "id": page["id"],
        "site": site,
        "time": moment,
        "hash": digest,
        "compress": compress,
    }
    entry_path = os.path.join(entry_dir, _stamp(moment) + ".json")
    with open(entry_path, "w", encoding="utf-8") as handle:
        json.dump(entry, handle)

    if retention_days is not None:
        prune(directory, retention_days)
    return text_path


def _iter_entries(index_dir):
    for root, _dirs, files in os.walk(index_dir):
        for name in files:
            if name.endswith(".json"):
                yield os.path.join(root, name)


def prune(directory, retention_days):
    """删除早于保留天数的索引条目，并清理不再被引用的对象。"""
    cutoff = time.time() - retention_days * SECONDS_PER_DAY
    index_dir = os.path.join(directory, INDEX_DIR)
    referenced = set()
    if os.path.isdir(index_dir):
        for entry_path in _iter_entries(index_dir):
            with open(entry_path, encoding="utf-8") as handle:
                entry = json.load(handle)
            if entry["time"] < cutoff:
                os.remove(entry_path)
            else:
                referenced.add(entry["hash"])
    objects_dir = os.path.join(directory, OBJECTS_DIR)
    if os.path.isdir(objects_dir):
        for name in os.listdir(objects_dir):
            digest = name.split(".")[0]
            if digest not in referenced:
                os.remove(os.path.join(objects_dir, name))


def get(page_id, directory, site="default", at=None):
    """按站点和时间点取回快照；at 为 None 时取最新。不存在返回 None。"""
    entry_dir = os.path.join(directory, INDEX_DIR, site, page_id)
    if not os.path.isdir(entry_dir):
        return None
    cutoff = _coerce_time(at) if at is not None else None
    best = None
    for name in sorted(os.listdir(entry_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(entry_dir, name), encoding="utf-8") as handle:
            entry = json.load(handle)
        if cutoff is not None and entry["time"] > cutoff:
            continue
        if best is None or entry["time"] > best["time"]:
            best = entry
    if best is None:
        return None
    text_path, meta_path = _object_paths(directory, best["hash"], best.get("compress", False))
    structure = []
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as handle:
            structure = json.load(handle).get("structure", [])
    return {
        "id": page_id,
        "site": site,
        "time": best["time"],
        "hash": best["hash"],
        "text": _read_text(text_path),
        "structure": structure,
    }


def _change_percent(a, b):
    if a == b:
        return 0.0
    return round((1 - SequenceMatcher(None, a, b).ratio()) * 100, 2)


def _structure_diff(struct_a, struct_b):
    added, removed = [], []
    matcher = SequenceMatcher(None, struct_a, struct_b)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            removed.extend(struct_a[i1:i2])
        if tag in ("insert", "replace"):
            added.extend(struct_b[j1:j2])
    percent = 0.0 if not struct_a and not struct_b else _change_percent(struct_a, struct_b)
    return added, removed, percent


def diff(a, b):
    """比对两个快照文件，输出文本变化、结构变化和增删节点。"""
    text_a = _read_text(a)
    text_b = _read_text(b)
    struct_a = _load_structure(a)
    struct_b = _load_structure(b)
    added, removed, structure_percent = _structure_diff(struct_a, struct_b)
    text_percent = _change_percent(text_a, text_b)
    return {
        "length_a": len(text_a),
        "length_b": len(text_b),
        "changed": text_a != text_b or struct_a != struct_b,
        "text_change_percent": text_percent,
        "structure_change_percent": structure_percent,
        "added_nodes": added,
        "removed_nodes": removed,
        "change_percent": round((text_percent + structure_percent) / 2, 2),
    }
