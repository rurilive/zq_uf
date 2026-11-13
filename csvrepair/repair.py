"""CSV修复：按schema定位列数异常和引号问题，能确定的按分隔符重切，不能确定的整行保留。"""
import csv
import hashlib
import json
import os


class RepairError(Exception):
    """输入或环境不满足修复条件时抛出，绝不静默跳过。"""


def _validate(path, schema, delimiter, quotechar, max_record_lines):
    if not isinstance(schema, (list, tuple)) or not schema:
        raise RepairError("schema必须是非空列名序列")
    if not all(isinstance(name, str) for name in schema):
        raise RepairError("schema列名必须都是字符串")
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise RepairError("delimiter必须是单个字符: %r" % (delimiter,))
    if not isinstance(quotechar, str) or len(quotechar) != 1:
        raise RepairError("quotechar必须是单个字符: %r" % (quotechar,))
    if delimiter == quotechar:
        raise RepairError("delimiter和quotechar不能相同")
    if not isinstance(max_record_lines, int) or max_record_lines < 1:
        raise RepairError("max_record_lines必须是正整数: %r" % (max_record_lines,))
    if not os.path.isfile(path):
        raise RepairError("文件不存在或不是普通文件: %s" % path)


def _read_records(path, width, delimiter, quotechar, max_record_lines):
    """按引号配对把物理行拼成逻辑记录；引号长期不闭合时按物理行拆开，避免吞掉后续行。"""
    records = []
    buffer = []
    start = 1
    try:
        handle = open(path, encoding="utf-8", newline="")
    except OSError as exc:
        raise RepairError("无法读取文件 %s: %s" % (path, exc))
    with handle:
        for lineno, line in enumerate(handle, 1):
            if not buffer:
                start = lineno
                stripped = line.rstrip("\r\n")
                if (stripped.count(quotechar) % 2 == 1
                        and len(stripped.split(delimiter)) == width):
                    records.append((lineno, [stripped]))
                    continue
            buffer.append(line.rstrip("\r\n"))
            if "\n".join(buffer).count(quotechar) % 2 == 0:
                records.append((start, buffer))
                buffer = []
            elif len(buffer) >= max_record_lines:
                for offset, single in enumerate(buffer):
                    records.append((start + offset, [single]))
                buffer = []
        if buffer:
            records.append((start, buffer))
    return records


def _repair_record(lines, width, delimiter, quotechar):
    """返回 (row, action, note)；row为None表示无法确定，整行保留。"""
    raw = "\n".join(lines)
    if raw.count(quotechar) % 2 == 0:
        fields = next(csv.reader([raw], delimiter=delimiter, quotechar=quotechar))
        if len(fields) == width:
            return fields, "joined" if len(lines) > 1 else "ok", None
        if len(fields) < width:
            return fields + [""] * (width - len(fields)), "padded", None
        return None, "kept", "列数%d超过schema宽度%d，无法确定切分" % (len(fields), width)
    naive = raw.split(delimiter)
    if len(naive) == width:
        return naive, "resplit", "引号未闭合，按分隔符重切"
    return None, "kept", "引号未闭合且列数%d与schema宽度%d不符" % (len(naive), width)


def repair(path, schema, delimiter=",", quotechar='"', max_record_lines=10):
    """修复CSV文件，返回行数、校验和与报告路径；异常输入抛出RepairError。"""
    _validate(path, schema, delimiter, quotechar, max_record_lines)
    width = len(schema)

    legacy_rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split(delimiter)
            if len(parts) < width:
                parts += [""] * (width - len(parts))
            legacy_rows.append(parts[:width])

    entries = []
    for lineno, lines in _read_records(path, width, delimiter, quotechar, max_record_lines):
        row, action, note = _repair_record(lines, width, delimiter, quotechar)
        entry = {"line": lineno, "raw": "\n".join(lines), "action": action, "row": row}
        if note:
            entry["note"] = note
        entries.append(entry)

    out = path + ".repaired"
    try:
        with open(out, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter, quotechar=quotechar,
                                lineterminator="\n")
            for entry in entries:
                if entry["row"] is None:
                    handle.write(entry["raw"] + "\n")
                else:
                    writer.writerow(entry["row"])
    except OSError as exc:
        raise RepairError("无法写出修复文件 %s: %s" % (out, exc))

    with open(out, "rb") as handle:
        checksum = hashlib.sha256(handle.read()).hexdigest()

    summary = {}
    for entry in entries:
        summary[entry["action"]] = summary.get(entry["action"], 0) + 1
    report = {
        "source": path,
        "output": out,
        "schema": list(schema),
        "delimiter": delimiter,
        "quotechar": quotechar,
        "max_record_lines": max_record_lines,
        "total": len(entries),
        "actions": summary,
        "checksum": checksum,
        "records": entries,
    }
    report_path = path + ".report.json"
    try:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        raise RepairError("无法写出报告 %s: %s" % (report_path, exc))

    return {
        "rows": legacy_rows,
        "count": len(entries),
        "out": out,
        "repaired": [e["row"] if e["row"] is not None else e["raw"] for e in entries],
        "actions": [e["action"] for e in entries],
        "report_path": report_path,
        "checksum": checksum,
    }
