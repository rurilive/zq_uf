"""按访问热度分热、温、冷三层，生成并执行迁移计划。

状态全部落在数据集目录内的 JSON 文件里，可序列化、可重启复现：
- .tiering_stats.json   访问统计：{逻辑名: {"hits": 次数, "last_access": 时间戳}}
- .tiering_catalog.json 迁移目录：{逻辑名: {"tier", "size", "updated_at"}}
热层文件常驻数据集根目录，温/冷层文件分别放在 warm/、cold/ 子目录。
"""
import json
import os
import shutil
import time

HOT_MIN_HITS = 10
HOT_MAX_IDLE_DAYS = 7
COLD_MAX_HITS = 2
COLD_MIN_IDLE_DAYS = 90

SECONDS_PER_DAY = 86400
STATS_FILE = ".tiering_stats.json"
CATALOG_FILE = ".tiering_catalog.json"
METADATA_FILES = (STATS_FILE, CATALOG_FILE)
TIERS = ("hot", "warm", "cold")
TIER_DIRS = {"warm": "warm", "cold": "cold"}


class TieringError(Exception):
    """分层或迁移过程中的明确错误，绝不静默跳过。"""


def classify(hits, last_access, now):
    """按命中次数和闲置天数判定层级。"""
    idle_days = max(0.0, (now - last_access) / SECONDS_PER_DAY)
    if hits >= HOT_MIN_HITS and idle_days <= HOT_MAX_IDLE_DAYS:
        return "hot"
    if hits <= COLD_MAX_HITS or idle_days >= COLD_MIN_IDLE_DAYS:
        return "cold"
    return "warm"


def record_access(dataset, name, hits=1, at=None):
    """记录访问，返回该文件的最新统计；统计持久化到 STATS_FILE。"""
    _require_dataset(dataset)
    _check_name(name)
    if not isinstance(hits, int) or hits < 1:
        raise TieringError("hits 必须是正整数: %r" % (hits,))
    at = time.time() if at is None else float(at)
    stats = _load_json(_stats_path(dataset), "访问统计")
    entry = stats.get(name) or {"hits": 0, "last_access": 0.0}
    entry["hits"] += hits
    entry["last_access"] = max(entry["last_access"], at)
    stats[name] = entry
    _write_json(_stats_path(dataset), stats)
    return dict(entry)


def plan(dataset, dry_run=False, now=None):
    """扫描数据集，按访问统计生成迁移计划；无统计的文件默认热层。"""
    _require_dataset(dataset)
    now = time.time() if now is None else float(now)
    stats = _load_json(_stats_path(dataset), "访问统计")
    moves = []
    for root, dirs, files in os.walk(dataset):
        dirs.sort()
        for filename in sorted(files):
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, dataset)
            if rel in METADATA_FILES:
                continue
            current_tier, name = _split_tier(rel)
            entry = stats.get(name)
            target = "hot" if entry is None else classify(
                entry["hits"], entry["last_access"], now)
            moves.append({
                "path": path,
                "name": name,
                "current_tier": current_tier,
                "tier": target,
                "size": os.path.getsize(path),
            })
    return {
        "dataset": dataset,
        "moves": moves,
        "dry_run": bool(dry_run),
        "generated_at": now,
    }


def apply(plan_payload):
    """执行迁移计划：搬文件、逐条校验、更新目录；干跑只预演不动盘。"""
    moves, dry_run = _check_payload(plan_payload)
    dataset = plan_payload["dataset"]
    relocated = [m for m in moves if m["current_tier"] != m["tier"]]
    freed = sum(m["size"] for m in relocated if m["current_tier"] == "hot")
    skipped = len(moves) - len(relocated)
    if dry_run:
        return {"moved": 0, "would_move": len(relocated), "skipped": skipped,
                "freed_bytes": freed, "dry_run": True}
    _require_dataset(dataset)
    catalog = _load_json(_catalog_path(dataset), "迁移目录")
    moved = 0
    for move in relocated:
        _relocate(dataset, move)
        catalog[move["name"]] = {
            "tier": move["tier"],
            "size": move["size"],
            "updated_at": time.time(),
        }
        moved += 1
    _write_json(_catalog_path(dataset), catalog)
    return {"moved": moved, "skipped": skipped,
            "freed_bytes": freed, "dry_run": False}


def _relocate(dataset, move):
    src = move["path"]
    dst = _destination(dataset, move["name"], move["tier"])
    if not os.path.isfile(src):
        raise TieringError("源文件缺失，无法迁移: %s" % src)
    if os.path.exists(dst):
        raise TieringError("目标文件已存在，拒绝覆盖: %s" % dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    if not os.path.isfile(dst) or os.path.getsize(dst) != move["size"]:
        raise TieringError("迁移校验失败（大小不符或目标缺失）: %s" % dst)
    if os.path.exists(src):
        raise TieringError("迁移校验失败（源文件未清除）: %s" % src)
    _prune_empty_dirs(dataset, os.path.dirname(src))


def _destination(dataset, name, tier):
    if tier == "hot":
        return os.path.join(dataset, name)
    if tier in TIER_DIRS:
        return os.path.join(dataset, TIER_DIRS[tier], name)
    raise TieringError("未知层级: %r" % (tier,))


def _split_tier(rel):
    parts = rel.split(os.sep)
    if parts[0] in TIER_DIRS:
        return parts[0], os.sep.join(parts[1:])
    return "hot", rel


def _prune_empty_dirs(dataset, path):
    root = os.path.abspath(dataset)
    while os.path.abspath(path).startswith(root + os.sep):
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
            path = os.path.dirname(path)
        else:
            return


def _check_payload(plan_payload):
    if not isinstance(plan_payload, dict):
        raise TieringError("非法的迁移计划: %r" % (plan_payload,))
    if not plan_payload.get("dataset"):
        raise TieringError("迁移计划缺少 dataset 字段")
    moves = plan_payload.get("moves")
    if not isinstance(moves, list):
        raise TieringError("迁移计划缺少 moves 列表")
    required = {"path", "name", "current_tier", "tier", "size"}
    for move in moves:
        missing = required - set(move)
        if missing:
            raise TieringError("迁移项缺少字段 %s: %r" % (sorted(missing), move))
        if move["tier"] not in TIERS or move["current_tier"] not in TIERS:
            raise TieringError("迁移项层级非法: %r" % (move,))
    return moves, bool(plan_payload.get("dry_run"))


def _require_dataset(dataset):
    if not isinstance(dataset, str) or not os.path.isdir(dataset):
        raise TieringError("数据集目录不存在: %r" % (dataset,))


def _check_name(name):
    if not name or os.path.isabs(name) or ".." in name.split(os.sep):
        raise TieringError("非法的文件逻辑名: %r" % (name,))


def _stats_path(dataset):
    return os.path.join(dataset, STATS_FILE)


def _catalog_path(dataset):
    return os.path.join(dataset, CATALOG_FILE)


def _load_json(path, label):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise TieringError("%s文件损坏: %s (%s)" % (label, path, exc))
    if not isinstance(data, dict):
        raise TieringError("%s文件格式非法: %s" % (label, path))
    return data


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
