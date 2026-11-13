"""CSV 导出：BOM、方言配置、文本化编号、列校验、流式写入、分片续传。"""
import csv
import json
import os
import re
import zlib

_DIGITS_RE = re.compile(r"\d+")
_UTF8_BOM = b"\xef\xbb\xbf"
# Excel 对超过 11 位的数字会转科学计数法，超过 15 位会丢精度。
_LONG_NUMBER_MIN = 12


class ExportError(ValueError):
    """导出校验失败，消息中包含行号和列名。"""


class _CrcStream:
    """文本写入包装器：边写边编码、边算 CRC32，内存占用 O(1)。"""

    def __init__(self, raw, encoding):
        self._raw = raw
        self._encoding = encoding
        self.crc = 0

    def write(self, text):
        data = text.encode(self._encoding)
        self.crc = zlib.crc32(data, self.crc)
        self._raw.write(data)
        return len(text)

    def flush(self):
        self._raw.flush()


def _as_text(value, long_number_min):
    """长数字和零开头编号转成 Excel 文本公式，避免科学计数和丢前导零。"""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if _DIGITS_RE.fullmatch(text) and (
        len(text) >= long_number_min or (len(text) > 1 and text.startswith("0"))
    ):
        return '="%s"' % text
    return text


def _normalize_row(row, columns, required_idx, row_num, long_number_min):
    if isinstance(row, dict):
        values = [row.get(name) for name in columns]
    else:
        values = list(row)
    if columns is not None and len(values) != len(columns):
        raise ExportError(
            "row %d: expected %d columns (%s), got %d"
            % (row_num, len(columns), ", ".join(columns), len(values))
        )
    for index in required_idx:
        if index < len(values) and values[index] in (None, ""):
            raise ExportError(
                "row %d: column '%s' is required but empty" % (row_num, columns[index])
            )
    return [_as_text(value, long_number_min) for value in values]


def _has_non_ascii(rows):
    return any(ord(ch) > 127 for row in rows for cell in row for ch in str(cell))


def _shard_path(path, index):
    stem, ext = os.path.splitext(path)
    return "%s.%04d%s" % (stem, index, ext)


def _manifest_path(path):
    stem, _ = os.path.splitext(path)
    return stem + ".manifest.json"


def _verify_shard(entry):
    shard = entry["file"]
    if not os.path.exists(shard):
        raise ExportError("cannot resume: shard missing: %s" % shard)
    crc = 0
    with open(shard, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            crc = zlib.crc32(chunk, crc)
    if crc != entry["crc32"]:
        raise ExportError("cannot resume: shard checksum mismatch: %s" % shard)


def export(rows, path, options=None):
    """把 rows 流式写入 path，返回 path。options 见 README。"""
    options = options or {}
    encoding = options.get("encoding", "utf-8")
    bom = options.get("bom")
    if encoding.replace("_", "-").lower() in ("utf-8-sig", "utf8-sig"):
        encoding, bom = "utf-8", True if bom is None else bom
    columns = options.get("columns")
    required = options.get("required") or []
    if required and not columns:
        raise ExportError("option 'required' needs 'columns' to name columns")
    required_idx = [columns.index(name) for name in required] if columns else []
    shard_size = options.get("shard_size")
    resume = options.get("resume", True)
    long_number_min = options.get("long_number_min", _LONG_NUMBER_MIN)
    dialect = {
        "delimiter": options.get("delimiter", ","),
        "quotechar": options.get("quotechar", '"'),
        "lineterminator": options.get("lineterminator", "\r\n"),
    }

    iterator = iter(rows)
    row_num = 0
    shard_index = 0
    shard_rows = 0
    total_rows = 0
    manifest = {"path": path, "columns": columns, "shards": [], "total_rows": 0}

    if shard_size:
        mpath = _manifest_path(path)
        if resume and os.path.exists(mpath):
            with open(mpath, encoding="utf-8") as handle:
                manifest = json.load(handle)
            for entry in manifest["shards"]:
                _verify_shard(entry)
            done = manifest["total_rows"]
            for _ in range(done):
                next(iterator, None)
            row_num = total_rows = done
            shard_index = len(manifest["shards"])

    state = {"raw": None, "stream": None, "writer": None}

    def decide_bom(first_row):
        if bom is not None or encoding != "utf-8":
            return bool(bom)
        probe = [columns] if columns else []
        if first_row is not None:
            probe.append(list(first_row) if not isinstance(first_row, dict) else list(first_row.values()))
        return _has_non_ascii(probe)

    def start_shard(first_row):
        nonlocal shard_index, shard_rows
        shard_index += 1
        shard_rows = 0
        use_bom = decide_bom(first_row)
        if shard_size:
            name = _shard_path(path, shard_index)
            manifest["shards"].append({"file": name, "rows": 0, "crc32": 0})
        else:
            name = path
        raw = open(name, "wb")
        stream = _CrcStream(raw, encoding)
        if use_bom:
            raw.write(_UTF8_BOM)
            stream.crc = zlib.crc32(_UTF8_BOM)
        state.update(raw=raw, stream=stream, writer=csv.writer(stream, **dialect))
        if columns:
            state["writer"].writerow(columns)

    def close_shard():
        if state["raw"] is not None:
            state["raw"].close()
        if shard_size and shard_rows:
            manifest["shards"][-1]["rows"] = shard_rows
            manifest["shards"][-1]["crc32"] = state["stream"].crc
        state.update(raw=None, stream=None, writer=None)

    pending = next(iterator, None)
    if pending is None:
        if shard_index == 0:
            if columns:
                start_shard(None)
                close_shard()
            else:
                open(path, "wb").close()
        if shard_size:
            manifest["total_rows"] = total_rows
            manifest["shards"] = [s for s in manifest["shards"] if s["rows"]]
            with open(_manifest_path(path), "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
        return path

    while pending is not None:
        if state["writer"] is None or (shard_size and shard_rows >= shard_size):
            close_shard()
            start_shard(pending)
        row_num += 1
        state["writer"].writerow(
            _normalize_row(pending, columns, required_idx, row_num, long_number_min)
        )
        shard_rows += 1
        total_rows += 1
        pending = next(iterator, None)
    close_shard()

    if shard_size:
        manifest["total_rows"] = total_rows
        manifest["shards"] = [s for s in manifest["shards"] if s["rows"]]
        with open(_manifest_path(path), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return path
