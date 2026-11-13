"""分区查询层：时间范围分区裁剪 + 条件下推。

接口保持 ``query(dataset, filters)`` 不变；查询过程是无状态的，
裁剪计划（``plan_query``）只由基本类型构成，可 JSON 序列化后落盘，
重启时用 ``execute_plan`` 原样复现。
"""
import json
import os
import time

ROOT = "warehouse"
PARTITION_SEPARATOR = ","
RANGE_OPS = ("gte", "gt", "lt", "lte")
SUPPORTED_OPS = ("eq", "ne") + RANGE_OPS


class PartQueryError(Exception):
    """partquery 所有显式错误的基类，异常路径绝不静默跳过。"""


class MissingPartitionError(PartQueryError):
    """过滤条件指向的分区在磁盘上不存在。"""


class InvalidPartitionError(PartQueryError):
    """分区目录里出现无法解析或结构不一致的分区文件。"""


class InvalidFilterError(PartQueryError):
    """过滤条件里出现不支持的算子或不可比较的取值。"""


class PartitionReadError(PartQueryError):
    """命中的分区文件无法读取或内容不是合法 JSONL。"""


def _normalize_filters(filters):
    """把 filters 归一化成 [(key, op, value), ...]，全部为基本类型。"""
    if filters is None:
        return []
    if not isinstance(filters, dict):
        raise InvalidFilterError("filters 必须是 dict，实际类型：%s" % type(filters).__name__)
    normalized = []
    for key, expression in filters.items():
        if not isinstance(key, str) or not key:
            raise InvalidFilterError("过滤字段名必须是非空字符串：%r" % (key,))
        if isinstance(expression, dict):
            if not expression:
                raise InvalidFilterError("字段 %s 的范围条件不能为空" % key)
            for op, value in expression.items():
                if op not in SUPPORTED_OPS:
                    raise InvalidFilterError(
                        "字段 %s 使用了不支持的算子 %r，支持：%s"
                        % (key, op, ", ".join(SUPPORTED_OPS)))
                normalized.append((key, op, value))
        else:
            normalized.append((key, "eq", expression))
    return normalized


def _parse_partition_name(filename):
    """解析 ``dt=2026-01-01.jsonl`` / ``dt=...,city=....jsonl`` 为有序字典。"""
    stem, ext = os.path.splitext(filename)
    if ext != ".jsonl" or not stem:
        raise InvalidPartitionError("分区文件必须以 .jsonl 结尾：%s" % filename)
    spec = {}
    for segment in stem.split(PARTITION_SEPARATOR):
        if "=" not in segment:
            raise InvalidPartitionError(
                "分区名 %r 缺少 key=value 段：%s" % (filename, segment))
        key, value = segment.split("=", 1)
        if not key or not value:
            raise InvalidPartitionError(
                "分区名 %r 存在空的 key 或 value：%s" % (filename, segment))
        if key in spec:
            raise InvalidPartitionError("分区名 %r 的字段 %s 重复" % (filename, key))
        spec[key] = value
    if not spec:
        raise InvalidPartitionError("分区名无法解析：%s" % filename)
    return spec


def _compare(left, op, right):
    try:
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
        if op == "lt":
            return left < right
        if op == "lte":
            return left <= right
    except TypeError:
        raise InvalidFilterError(
            "取值不可比较：%r %s %r" % (left, op, right)) from None
    raise InvalidFilterError("不支持的算子：%s" % op)


def _spec_matches(spec, predicates):
    """分区裁剪：分区值都是字符串，按字典序比较（ISO 日期字典序即时间序）。"""
    for key, op, value in predicates:
        if key not in spec:
            return False
        if not _compare(spec[key], op, str(value)):
            return False
    return True


def _row_matches(row, predicates):
    """行级下推：保留条件在读取 JSONL 时逐行判定，缺字段一律不命中。"""
    for key, op, value in predicates:
        if key not in row:
            return False
        if not _compare(row[key], op, value):
            return False
    return True


def plan_query(dataset, filters):
    """生成裁剪计划；返回值只含 str/dict/list 等基本类型，可 json.dumps。"""
    predicates = _normalize_filters(filters)
    base = os.path.join(ROOT, dataset)

    if not os.path.isdir(base):
        partition_keys = {key for key, _, _ in predicates}
        if partition_keys:
            raise MissingPartitionError(
                "数据集 %r 不存在，无法满足分区条件：%s"
                % (dataset, sorted(partition_keys)))
        return {
            "dataset": dataset,
            "base": base,
            "filters": predicates,
            "partitions": [],
            "scanned_total": 0,
        }

    entries = sorted(os.listdir(base))
    specs = {}
    schema = None
    for name in entries:
        if name.startswith("."):
            continue
        path = os.path.join(base, name)
        if os.path.isdir(path):
            continue
        spec = _parse_partition_name(name)
        keys = tuple(sorted(spec))
        if schema is None:
            schema = keys
        elif keys != schema:
            raise InvalidPartitionError(
                "分区字段不一致：%s 的字段为 %s，期望 %s" % (name, keys, schema))
        specs[name] = spec

    partition_fields = set(schema or ())
    partition_predicates = [p for p in predicates if p[0] in partition_fields]
    row_predicates = [p for p in predicates if p[0] not in partition_fields]

    if partition_predicates:
        selected = sorted(
            name for name, spec in specs.items()
            if _spec_matches(spec, partition_predicates))
        if not selected:
            requested = {
                key: [value for k, op, value in partition_predicates if k == key]
                for key, _, _ in partition_predicates}
            raise MissingPartitionError(
                "数据集 %r 没有满足分区条件 %s 的分区（现有分区：%s）"
                % (dataset, requested, sorted(specs) or "无"))
    else:
        selected = sorted(specs)

    return {
        "dataset": dataset,
        "base": base,
        "filters": predicates,
        "partition_filters": partition_predicates,
        "row_filters": row_predicates,
        "partitions": selected,
        "scanned_total": len(specs),
    }


def execute_plan(plan):
    """执行可序列化计划；重启后只要分区文件仍在，结果即可复现。"""
    started = time.perf_counter()
    base = plan["base"]
    selected = plan["partitions"]
    row_predicates = [tuple(p) for p in plan.get("row_filters", plan["filters"])]

    rows = []
    row_count = 0
    for name in selected:
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            raise MissingPartitionError(
                "计划中的分区在执行时已不存在：%s" % path)
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise PartitionReadError(
                            "分区文件 %s 出现非法 JSON 行：%s" % (path, exc)) from None
                    row_count += 1
                    if _row_matches(row, row_predicates):
                        rows.append(row)
        except OSError as exc:
            raise PartitionReadError("分区文件 %s 读取失败：%s" % (path, exc)) from None

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return {
        "rows": rows,
        "scanned": len(selected),
        "missing": False,
        "partitions": list(selected),
        "scanned_total": plan.get("scanned_total", len(selected)),
        "row_count": row_count,
        "elapsed_ms": elapsed_ms,
    }


def query(dataset, filters):
    """按分区裁剪 + 条件下推执行查询，接口与历史版本保持一致。"""
    plan = plan_query(dataset, filters)
    return execute_plan(plan)
