"""reconcheck 核心：流式归并对账，输出可定位的差异报告。

compare(left, right, config) 按单键或组合键全量匹配，输出三类差异：
left_only（仅左有）、right_only（仅右有）、mismatches（字段不一致）。
重复主键记入 duplicate_keys 并判失败；命中例外规则的差异降级为 warnings。
"""

import heapq
import os
import pickle
import tempfile
from concurrent.futures import ThreadPoolExecutor
from itertools import islice

DEFAULT_KEYS = ("id",)
MISSING = "<missing>"


def compare(left, right, config=None):
    """全量对账，返回可定位的差异报告。

    config 支持的项：
      keys              键字段列表（默认 ["id"]），多字段即组合键
      tolerance         默认数值误差 {"abs": ..., "rel": ...}
      field_tolerances  按字段覆盖默认误差 {field: {"abs": ..., "rel": ...}}
      partition_by      分区字段，按分区并行对账
      workers           分区并行度（默认 4）
      stream_chunk_size 流式外排的块大小（默认 10000）
      exceptions        例外规则列表，命中的差异降级为 warnings
    """
    cfg = dict(config or {})
    keys = cfg.get("keys") or DEFAULT_KEYS
    key_fields = (keys,) if isinstance(keys, str) else tuple(keys)
    tolerance = _tolerance(cfg.get("tolerance"))
    field_tolerances = {
        field: _tolerance(tol)
        for field, tol in (cfg.get("field_tolerances") or {}).items()
    }
    exceptions = list(cfg.get("exceptions") or [])
    chunk_size = max(1, int(cfg.get("stream_chunk_size") or 10000))
    partition_by = cfg.get("partition_by")

    if partition_by:
        report, rows_left, rows_right = _compare_by_partition(
            left, right, partition_by, key_fields, tolerance,
            field_tolerances, chunk_size, cfg)
    else:
        left_sorted, rows_left = _sorted_rows(left, key_fields, chunk_size)
        right_sorted, rows_right = _sorted_rows(right, key_fields, chunk_size)
        report = _reconcile(left_sorted, right_sorted, key_fields,
                            tolerance, field_tolerances, skip_fields=())
    report["rows_left"] = rows_left
    report["rows_right"] = rows_right
    return _finalize(report, exceptions)


# ---------------------------------------------------------------- 键与精度

def _key_of(row, key_fields):
    return tuple(row.get(field) for field in key_fields)


def _render_key(key):
    return key[0] if len(key) == 1 else tuple(key)


def _norm_key(key):
    return tuple(key) if isinstance(key, (list, tuple)) else (key,)


def _tolerance(spec):
    if spec is None:
        return {"abs": 0.0, "rel": 0.0}
    if isinstance(spec, (int, float)):
        return {"abs": float(spec), "rel": 0.0}
    return {"abs": float(spec.get("abs", 0.0)), "rel": float(spec.get("rel", 0.0))}


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _within(left, right, tolerance):
    diff = abs(left - right)
    limit = max(tolerance["abs"],
                tolerance["rel"] * max(abs(left), abs(right)))
    return diff <= limit


# ---------------------------------------------------------------- 流式归并

def _sorted_rows(rows, key_fields, chunk_size):
    """返回 (按键有序的行迭代器, 行数)。迭代器输入走外排，内存有界。"""
    if isinstance(rows, (list, tuple)):
        ordered = sorted(rows, key=lambda row: _key_of(row, key_fields))
        return iter(ordered), len(rows)
    return _external_sorted(rows, key_fields, chunk_size)


def _write_spill(chunk):
    fd, path = tempfile.mkstemp(prefix="reconcheck-", suffix=".spill")
    with os.fdopen(fd, "wb") as fh:
        pickle.dump(chunk, fh)
    return path


def _read_spill(path):
    with open(path, "rb") as fh:
        while True:
            try:
                chunk = pickle.load(fh)
            except EOFError:
                return
            for row in chunk:
                yield row


def _external_sorted(rows, key_fields, chunk_size):
    spills = []
    count = 0
    iterator = iter(rows)
    while True:
        chunk = list(islice(iterator, chunk_size))
        if not chunk:
            break
        count += len(chunk)
        chunk.sort(key=lambda row: _key_of(row, key_fields))
        spills.append(_write_spill(chunk))
    streams = [_read_spill(path) for path in spills]
    if streams:
        merged = heapq.merge(*streams, key=lambda row: _key_of(row, key_fields))
    else:
        merged = iter(())

    def generate():
        try:
            yield from merged
        finally:
            for stream in streams:
                stream.close()
            for path in spills:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    return generate(), count


def _groups(sorted_rows, key_fields):
    """把有序行流按键分组，yield (key, [rows])。"""
    iterator = iter(sorted_rows)
    try:
        row = next(iterator)
    except StopIteration:
        return
    current_key = _key_of(row, key_fields)
    bucket = [row]
    for row in iterator:
        key = _key_of(row, key_fields)
        if key == current_key:
            bucket.append(row)
        else:
            yield current_key, bucket
            current_key, bucket = key, [row]
    yield current_key, bucket


def _merge_groups(left_sorted, right_sorted, key_fields):
    """归并两个按键分组的有序流，yield (key, left_rows, right_rows)。"""
    left_groups = _groups(left_sorted, key_fields)
    right_groups = _groups(right_sorted, key_fields)
    left = next(left_groups, None)
    right = next(right_groups, None)
    while left is not None or right is not None:
        if right is None or (left is not None and left[0] < right[0]):
            yield left[0], left[1], []
            left = next(left_groups, None)
        elif left is None or right[0] < left[0]:
            yield right[0], [], right[1]
            right = next(right_groups, None)
        else:
            yield left[0], left[1], right[1]
            left = next(left_groups, None)
            right = next(right_groups, None)


# ---------------------------------------------------------------- 差异计算

def _row_diffs(left_row, right_row, key_fields, skip_fields, tolerance,
               field_tolerances):
    fields = (set(left_row) | set(right_row)) - set(key_fields) - set(skip_fields)
    diffs = []
    for field in sorted(fields):
        left_value = left_row.get(field, MISSING)
        right_value = right_row.get(field, MISSING)
        if left_value is MISSING or right_value is MISSING:
            diffs.append({"field": field, "left": left_value, "right": right_value})
        elif _is_number(left_value) and _is_number(right_value):
            tol = field_tolerances.get(field, tolerance)
            if not _within(left_value, right_value, tol):
                diffs.append({"field": field, "left": left_value, "right": right_value})
        elif left_value != right_value:
            diffs.append({"field": field, "left": left_value, "right": right_value})
    return diffs


def _reconcile(left_sorted, right_sorted, key_fields, tolerance,
               field_tolerances, skip_fields):
    report = {"left_only": [], "right_only": [], "mismatches": [],
              "duplicate_keys": {"left": [], "right": []}}
    for key, left_rows, right_rows in _merge_groups(left_sorted, right_sorted,
                                                    key_fields):
        if len(left_rows) > 1:
            report["duplicate_keys"]["left"].append(key)
        if len(right_rows) > 1:
            report["duplicate_keys"]["right"].append(key)
        if len(left_rows) != 1 or len(right_rows) != 1:
            if len(left_rows) == 1:
                report["left_only"].append(key)
            elif len(right_rows) == 1:
                report["right_only"].append(key)
            continue
        for diff in _row_diffs(left_rows[0], right_rows[0], key_fields,
                               skip_fields, tolerance, field_tolerances):
            report["mismatches"].append({"key": key, **diff})
    return report


# ---------------------------------------------------------------- 分区并行

def _partition(rows, part_field, chunk_size):
    """按分区字段拆分行。返回 ({分区值: 行列表或spill路径}, 行数, spill文件列表)。"""
    if isinstance(rows, (list, tuple)):
        parts = {}
        for row in rows:
            parts.setdefault(row.get(part_field), []).append(row)
        return parts, len(rows), []
    paths = {}
    buffers = {}
    count = 0

    def flush(part):
        buffer = buffers.pop(part, None)
        if not buffer:
            return
        path = paths.get(part)
        if path is None:
            fd, path = tempfile.mkstemp(prefix="reconcheck-part-", suffix=".spill")
            os.close(fd)
            paths[part] = path
        with open(path, "ab") as fh:
            pickle.dump(buffer, fh)

    for row in rows:
        count += 1
        part = row.get(part_field)
        buffers.setdefault(part, []).append(row)
        if len(buffers[part]) >= chunk_size:
            flush(part)
    for part in list(buffers):
        flush(part)
    return paths, count, list(paths.values())


def _source_rows(source):
    return source if isinstance(source, list) else _read_spill(source)


def _compare_by_partition(left, right, part_field, key_fields, tolerance,
                          field_tolerances, chunk_size, cfg):
    left_parts, rows_left, left_spills = _partition(left, part_field, chunk_size)
    right_parts, rows_right, right_spills = _partition(right, part_field, chunk_size)
    try:
        report = {
            "left_only": [], "right_only": [], "mismatches": [],
            "duplicate_keys": {"left": [], "right": []},
            "missing_partitions": {
                "left_only": sorted(set(right_parts) - set(left_parts)),
                "right_only": sorted(set(left_parts) - set(right_parts)),
            },
        }
        common = sorted(set(left_parts) & set(right_parts))
        workers = max(1, int(cfg.get("workers") or 4))

        def reconcile_part(part):
            left_sorted, _ = _sorted_rows(_source_rows(left_parts[part]),
                                          key_fields, chunk_size)
            right_sorted, _ = _sorted_rows(_source_rows(right_parts[part]),
                                           key_fields, chunk_size)
            return _reconcile(left_sorted, right_sorted, key_fields, tolerance,
                              field_tolerances, skip_fields=(part_field,))

        if workers > 1 and len(common) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                sub_reports = list(pool.map(reconcile_part, common))
        else:
            sub_reports = [reconcile_part(part) for part in common]
        for sub in sub_reports:
            report["left_only"].extend(sub["left_only"])
            report["right_only"].extend(sub["right_only"])
            report["mismatches"].extend(sub["mismatches"])
            report["duplicate_keys"]["left"].extend(sub["duplicate_keys"]["left"])
            report["duplicate_keys"]["right"].extend(sub["duplicate_keys"]["right"])
        for part in report["missing_partitions"]["right_only"]:
            for row in _source_rows(left_parts[part]):
                report["left_only"].append(_key_of(row, key_fields))
        for part in report["missing_partitions"]["left_only"]:
            for row in _source_rows(right_parts[part]):
                report["right_only"].append(_key_of(row, key_fields))
        return report, rows_left, rows_right
    finally:
        for path in left_spills + right_spills:
            try:
                os.unlink(path)
            except OSError:
                pass


# ---------------------------------------------------------------- 例外与定稿

def _rule_matches(rule, diff):
    keys = rule.get("keys")
    if keys is not None and _norm_key(diff["key"]) not in {
            _norm_key(key) for key in keys}:
        return False
    fields = rule.get("fields")
    if fields is None and "field" in rule:
        fields = [rule["field"]]
    if fields is not None and diff.get("field") not in fields:
        return False
    if "max_abs" in rule or "max_rel" in rule:
        left, right = diff.get("left"), diff.get("right")
        if not (_is_number(left) and _is_number(right)):
            return False
        tol = {"abs": float(rule.get("max_abs", 0.0)),
               "rel": float(rule.get("max_rel", 0.0))}
        if not _within(left, right, tol):
            return False
    return True


def _sort_keys(keys):
    try:
        return sorted(keys)
    except TypeError:
        return sorted(keys, key=repr)


def _finalize(report, exceptions):
    def hit(diff):
        return any(_rule_matches(rule, diff) for rule in exceptions)

    warnings = []
    mismatches = []
    for diff in report["mismatches"]:
        entry = {"key": _render_key(diff["key"]), "field": diff["field"],
                 "left": diff["left"], "right": diff["right"]}
        if hit(diff):
            warnings.append({"type": "mismatch", **entry})
        else:
            mismatches.append(entry)
    left_only, right_only = [], []
    for key in report["left_only"]:
        if hit({"key": key, "field": None, "left": None, "right": None}):
            warnings.append({"type": "left_only", "key": _render_key(key)})
        else:
            left_only.append(key)
    for key in report["right_only"]:
        if hit({"key": key, "field": None, "left": None, "right": None}):
            warnings.append({"type": "right_only", "key": _render_key(key)})
        else:
            right_only.append(key)

    result = {
        "left_only": [_render_key(key) for key in _sort_keys(left_only)],
        "right_only": [_render_key(key) for key in _sort_keys(right_only)],
        "mismatches": sorted(mismatches,
                             key=lambda d: (repr(d["key"]), d["field"])),
        "duplicate_keys": {
            "left": [_render_key(key)
                     for key in _sort_keys(report["duplicate_keys"]["left"])],
            "right": [_render_key(key)
                      for key in _sort_keys(report["duplicate_keys"]["right"])],
        },
        "warnings": warnings,
        "rows_left": report["rows_left"],
        "rows_right": report["rows_right"],
    }
    if "missing_partitions" in report:
        result["missing_partitions"] = report["missing_partitions"]
    failed = (result["left_only"] or result["right_only"] or result["mismatches"]
              or result["duplicate_keys"]["left"] or result["duplicate_keys"]["right"]
              or any(result.get("missing_partitions", {}).values()))
    result["status"] = "fail" if failed else "pass"
    return result
