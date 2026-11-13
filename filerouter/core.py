"""按规则把采集文件分发到目标目录。"""
import datetime
import json
import os
import shutil

PENDING_DIR = "_pending"
REASON_SUFFIX = ".reason"
CONTENT_SNIFF_BYTES = 65536

DEFAULT_RULES = [
    {"name": "log", "suffix": "log"},
    {"name": "archive", "suffix": ["zip", "gz", "tar", "7z"]},
    {"name": "image", "suffix": ["jpg", "jpeg", "png", "gif"]},
]


def sort_rules(rules):
    """按 priority 从大到小排序（缺省 0），同优先级保持书写顺序。"""
    return sorted(rules, key=lambda rule: -rule.get("priority", 0))


def load_rules(config_path):
    """从 JSON 加载规则，格式为 {"rules": [...]} 或直接是规则列表。"""
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rules = data["rules"] if isinstance(data, dict) else data
    return sort_rules(rules)


def _as_list(value):
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _matches(rule, name, size, head):
    if "suffix" in rule:
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        suffixes = [str(s).lstrip(".").lower() for s in _as_list(rule["suffix"])]
        if ext not in suffixes:
            return False
    if "prefix" in rule:
        if not any(name.startswith(p) for p in _as_list(rule["prefix"])):
            return False
    if "min_size" in rule and size < rule["min_size"]:
        return False
    if "max_size" in rule and size > rule["max_size"]:
        return False
    if "contains" in rule:
        needle = rule["contains"]
        if isinstance(needle, str):
            needle = needle.encode("utf-8")
        if needle not in head:
            return False
    return True


def match_rule(path, rules=None):
    """返回第一个命中规则的名字，无命中返回 None。"""
    rules = sort_rules(rules if rules is not None else DEFAULT_RULES)
    name = os.path.basename(path)
    size = os.path.getsize(path)
    head = b""
    if any("contains" in rule for rule in rules):
        with open(path, "rb") as f:
            head = f.read(CONTENT_SNIFF_BYTES)
    for rule in rules:
        if _matches(rule, name, size, head):
            return rule["name"]
    return None


def route(path, target_root, rules=None, source=None, date=None):
    """把 path 分发到 target_root/<日期>/<来源>/<规则名>/ 下，重名加序号。

    命中返回目标路径；失败（无规则命中或读写出错）把文件移入
    target_root/_pending/ 并记录原因，返回待处理路径。
    """
    date = date or datetime.date.today().isoformat()
    source = source or _default_source(path)
    try:
        rule_name = match_rule(path, rules)
    except OSError as exc:
        return _to_pending(path, target_root, source, "read failed: %s" % exc)
    if rule_name is None:
        return _to_pending(path, target_root, source, "no matching rule")
    target_dir = os.path.join(target_root, date, source, rule_name)
    os.makedirs(target_dir, exist_ok=True)
    target = _dedup(target_dir, os.path.basename(path))
    try:
        shutil.move(path, target)
    except OSError as exc:
        return _to_pending(path, target_root, source, "move failed: %s" % exc)
    return target


def retry_pending(target_root, rules=None):
    """重跑待处理目录：逐个重新分发，成功的清掉原因记录。

    返回 [(文件名, 结果路径), ...]，仍失败的留在待处理目录并更新原因。
    """
    pending_dir = os.path.join(target_root, PENDING_DIR)
    results = []
    if not os.path.isdir(pending_dir):
        return results
    for name in sorted(os.listdir(pending_dir)):
        if name.endswith(REASON_SUFFIX):
            continue
        src = os.path.join(pending_dir, name)
        if not os.path.isfile(src):
            continue
        sidecar = src + REASON_SUFFIX
        source = None
        if os.path.exists(sidecar):
            source = read_reason(src).get("source")
        out = route(src, target_root, rules=rules, source=source)
        results.append((name, out))
        if os.path.dirname(os.path.abspath(out)) != os.path.abspath(pending_dir):
            if os.path.exists(sidecar):
                os.remove(sidecar)
    return results


def read_reason(path):
    """读取待处理文件的 sidecar，返回 {"source": ..., "reason": ...}。"""
    with open(path + REASON_SUFFIX, "r", encoding="utf-8") as f:
        return json.load(f)


def _default_source(path):
    return os.path.basename(os.path.dirname(os.path.abspath(path))) or "unknown"


def _dedup(target_dir, filename):
    candidate = os.path.join(target_dir, filename)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(filename)
    seq = 1
    while True:
        candidate = os.path.join(target_dir, "%s-%d%s" % (stem, seq, ext))
        if not os.path.exists(candidate):
            return candidate
        seq += 1


def _to_pending(path, target_root, source, reason):
    pending_dir = os.path.join(target_root, PENDING_DIR)
    os.makedirs(pending_dir, exist_ok=True)
    if os.path.dirname(os.path.abspath(path)) == os.path.abspath(pending_dir):
        target = path
    else:
        target = _dedup(pending_dir, os.path.basename(path))
        shutil.move(path, target)
    with open(target + REASON_SUFFIX, "w", encoding="utf-8") as f:
        json.dump({"source": source, "reason": reason}, f, ensure_ascii=False)
    return target
