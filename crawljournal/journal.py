"""采集日志：带长度、序号和 CRC32 校验的预写日志（WAL），纯标准库。

记录格式（每行一条 JSON，UTF-8）：
    {"len":N,"seq":S,"checksum":"xxxxxxxx","data":{...}}

len/checksum 针对 data 的规范化 JSON（sort_keys、紧凑分隔符）UTF-8 字节计算。
append 整行追加并 fsync；recover 从尾部反向扫描，丢弃半行与校验失败记录，
保留最后一段连续序号，截断尾部垃圾后返回有效记录。
"""
import json
import os
import zlib

_MAGIC = b'{"len":'


class Recovery(list):
    """recover 的返回值：有效记录列表，附带恢复统计。"""

    def __init__(self, records, discarded=0, last_seq=None, seqs=()):
        super().__init__(records)
        self.discarded = discarded
        self.last_seq = last_seq
        self.seqs = list(seqs)


def _canonical(record):
    return json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _checksum(payload):
    return format(zlib.crc32(payload) & 0xFFFFFFFF, "08x")


def _parse_frame(line):
    """校验一行，返回 (seq, data)；任何校验失败返回 None。"""
    try:
        frame = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(frame, dict) or "data" not in frame:
        return None
    length, seq, checksum = frame.get("len"), frame.get("seq"), frame.get("checksum")
    if not isinstance(length, int) or not isinstance(seq, int):
        return None
    if not isinstance(checksum, str):
        return None
    payload = _canonical(frame["data"])
    if len(payload) != length or _checksum(payload) != checksum:
        return None
    return seq, frame["data"]


def _scan(raw, path):
    """逐行校验，返回 (frames, discarded)；frames 为 (seq, data, 原始行字节)。"""
    frames = []
    discarded = 0
    pos = 0
    while True:
        nl = raw.find(b"\n", pos)
        if nl < 0:
            break
        parsed = _parse_frame(raw[pos:nl]) if nl > pos else None
        if parsed is None:
            discarded += 1
        else:
            frames.append((parsed[0], parsed[1], raw[pos:nl + 1]))
        pos = nl + 1
    partial = raw[pos:]
    if partial:
        if partial.startswith(_MAGIC):
            discarded += 1  # 崩溃留下的半行，丢弃
        else:
            raise ValueError(
                "crawljournal: 尾部第 %d 字节起出现无法识别的数据，"
                "不像帧残片，拒绝静默丢弃: %r" % (pos, partial[:40])
            )
    return frames, discarded


def _next_seq(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return 1
    last = 0
    for line in raw.split(b"\n"):
        if line:
            parsed = _parse_frame(line)
            if parsed is not None:
                last = parsed[0]
    return last + 1


def append(record, path):
    """追加一条带长度、序号和校验和的记录，写盘后 fsync。"""
    payload = _canonical(record)  # 不可序列化时抛 TypeError
    frame = {
        "len": len(payload),
        "seq": _next_seq(path),
        "checksum": _checksum(payload),
        "data": record,
    }
    line = json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n"
    prefix = ""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                prefix = "\n"  # 先终止崩溃留下的半行，避免与新记录粘连
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(prefix + line)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def recover(path):
    """恢复日志：反向扫描，丢弃半行/坏校验记录，保留最后一段连续序号。

    自动截断尾部垃圾并 fsync，返回 Recovery（有效记录列表，
    附 discarded/last_seq/seqs）。文件不存在抛 FileNotFoundError；
    尾部出现无法识别的数据抛 ValueError。
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    frames, discarded = _scan(raw, path)

    seen = set()
    unique = []
    for entry in frames:
        if entry[0] in seen:
            discarded += 1  # 重复序号只保留第一条
            continue
        seen.add(entry[0])
        unique.append(entry)

    run = []
    for entry in reversed(unique):
        if run and entry[0] != run[0][0] - 1:
            break
        run.insert(0, entry)
    discarded += len(unique) - len(run)

    kept = b"".join(entry[2] for entry in run)
    if kept != raw:
        # 把保留的帧原样写回，截掉尾部垃圾和中间坏行；帧自校验，重写中途崩溃仍可恢复
        with open(path, "r+b") as handle:
            handle.write(kept)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())

    last_seq = run[-1][0] if run else None
    if discarded:
        print(
            "crawljournal: 丢弃 %d 条记录；最后有效序号: %s" % (discarded, last_seq)
        )
    return Recovery(
        [entry[1] for entry in run],
        discarded=discarded,
        last_seq=last_seq,
        seqs=[entry[0] for entry in run],
    )
