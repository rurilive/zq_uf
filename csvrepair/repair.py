"""CSV修复：按schema定位列数异常和引号问题，写出修复文件和报告。"""
import csv
import hashlib
import json
import os

DELIMITER = ","
QUOTECHAR = '"'
MAX_JOIN_LINES = 20

OK = "ok"
RESPLIT = "resplit"
PADDED = "padded"
JOINED = "joined"
KEPT_RAW = "kept_raw"


def _validate(path, schema):
    if not isinstance(schema, (list, tuple)) or not schema:
        raise ValueError("schema必须是非空列名列表: %r" % (schema,))
    for name in schema:
        if not isinstance(name, str) or not name:
            raise ValueError("schema列名必须是非空字符串: %r" % (name,))
    if not os.path.exists(path):
        raise FileNotFoundError("输入文件不存在: %s" % path)
    if os.path.isdir(path):
        raise IsADirectoryError("输入路径是目录不是文件: %s" % path)


def _parse(text):
    return list(csv.reader(
        [text], delimiter=DELIMITER, quotechar=QUOTECHAR, strict=True
    ))[0]


def _naive_rows(lines, width):
    rows = []
    for line in lines:
        parts = line.split(DELIMITER)
        if len(parts) < width:
            parts += [""] * (width - len(parts))
        rows.append(parts[:width])
    return rows


def _record(line_no, action, raw, fields=None, reason=None):
    return {
        "line": line_no,
        "action": action,
        "raw": raw,
        "fields": fields,
        "reason": reason,
    }


def _repair_lines(lines, width):
    records = []
    index = 0
    while index < len(lines):
        line = lines[index]
        line_no = index + 1
        try:
            fields = _parse(line)
        except csv.Error:
            joined = line
            end = index
            fields = None
            while (end + 1 < len(lines)
                   and end - index + 1 < MAX_JOIN_LINES):
                end += 1
                joined += "\n" + lines[end]
                try:
                    fields = _parse(joined)
                    break
                except csv.Error:
                    continue
            if fields is not None and len(fields) == width:
                records.append(_record(line_no, JOINED, joined, fields))
                index = end + 1
                continue
            records.append(_record(
                line_no, KEPT_RAW, line, reason="unclosed_quote"))
            index += 1
            continue
        if len(fields) == width:
            action = RESPLIT if fields != line.split(DELIMITER) else OK
            records.append(_record(line_no, action, line, fields))
        elif len(fields) < width:
            fields += [""] * (width - len(fields))
            records.append(_record(line_no, PADDED, line, fields))
        else:
            records.append(_record(
                line_no, KEPT_RAW, line, reason="column_overflow"))
        index += 1
    return records


def _write_output(out, records):
    with open(out, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(
            handle, delimiter=DELIMITER, quotechar=QUOTECHAR,
            lineterminator="\n")
        for record in records:
            if record["action"] == KEPT_RAW:
                handle.write(record["raw"] + "\n")
            else:
                writer.writerow(record["fields"])


def _checksum(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _write_report(report_path, path, out, schema, lines, records, checksum):
    actions = []
    for record in records:
        if record["action"] == OK:
            continue
        entry = {
            "line": record["line"],
            "action": record["action"],
            "raw": record["raw"],
        }
        if record["reason"]:
            entry["reason"] = record["reason"]
        actions.append(entry)
    report = {
        "source": path,
        "output": out,
        "schema": list(schema),
        "delimiter": DELIMITER,
        "quotechar": QUOTECHAR,
        "max_join_lines": MAX_JOIN_LINES,
        "input_lines": len(lines),
        "output_rows": len(records),
        "fixed_rows": sum(
            1 for r in records if r["action"] in (RESPLIT, PADDED, JOINED)),
        "kept_raw_rows": sum(
            1 for r in records if r["action"] == KEPT_RAW),
        "sha256": checksum,
        "actions": actions,
    }
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def repair(path, schema):
    """修复CSV文件，返回兼容旧契约的结果字典。

    rows/count/out保持旧行为（朴素切分）；修复结果写入out文件，
    原始行和修复动作写入path + ".report"，可用sha256校验和核对。
    """
    _validate(path, schema)
    width = len(schema)
    with open(path, encoding="utf-8") as handle:
        content = handle.read()
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [line.rstrip("\r") for line in lines]

    records = _repair_lines(lines, width)

    out = path + ".repaired"
    _write_output(out, records)
    checksum = _checksum(out)

    report_path = path + ".report"
    _write_report(report_path, path, out, schema, lines, records, checksum)

    rows = _naive_rows(lines, width)
    return {
        "rows": rows,
        "repaired": [
            record["fields"] if record["fields"] is not None
            else record["raw"]
            for record in records
        ],
        "count": len(rows),
        "out": out,
        "checksum": checksum,
        "report_path": report_path,
    }
