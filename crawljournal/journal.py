"""带校验的预写日志：记录带长度、序号和CRC32，追加即fsync，崩溃可恢复。"""
import json
import os
import zlib

_FRAME_PREFIX = '{"len"'


class RecoveryResult(list):
    """恢复出的记录列表，附带丢弃条数(dropped)和最后有效序号(last_seq)。"""

    def __init__(self, records, dropped=0, last_seq=None):
        super().__init__(records)
        self.dropped = dropped
        self.last_seq = last_seq


def _encode_frame(record, seq):
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
    frame = {
        "len": len(payload),
        "seq": seq,
        "checksum": format(zlib.crc32(payload) & 0xFFFFFFFF, "08x"),
        "payload": record,
    }
    return json.dumps(frame, ensure_ascii=False) + "\n"


def _decode_frame(line):
    """解析一行帧，返回(seq, payload, 校验是否通过)；不是合法帧返回None。"""
    try:
        frame = json.loads(line)
    except ValueError:
        return None
    if not isinstance(frame, dict) or "payload" not in frame:
        return None
    length, seq, checksum = frame.get("len"), frame.get("seq"), frame.get("checksum")
    if not (isinstance(length, int) and isinstance(seq, int) and isinstance(checksum, str)):
        return None
    data = json.dumps(frame["payload"], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ok = len(data) == length and format(zlib.crc32(data) & 0xFFFFFFFF, "08x") == checksum
    return seq, frame["payload"], ok


def _last_seq(path):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return 0
    for line in reversed(lines):
        decoded = _decode_frame(line)
        if decoded and decoded[2]:
            return decoded[0]
    return 0


def _truncate_torn_tail(handle):
    """截掉崩溃留下的未换行尾部，保证新帧从行首开始写。"""
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size == 0:
        return
    handle.seek(size - 1)
    if handle.read(1) == b"\n":
        return
    handle.seek(0)
    content = handle.read()
    handle.truncate(content.rfind(b"\n") + 1)


def append(record, path):
    try:
        json.dumps(record)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"record is not JSON serializable: {exc}") from exc
    seq = _last_seq(path) + 1
    data = _encode_frame(record, seq).encode("utf-8")
    with open(path, "a+b") as handle:
        _truncate_torn_tail(handle)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def recover(path):
    """反向扫描恢复：丢弃半行和校验失败记录，保留尾部连续序号段。"""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"journal log not found: {path}") from None
    except UnicodeDecodeError as exc:
        raise ValueError(f"journal log is not valid utf-8: {path}: {exc}") from exc

    lines = text.split("\n")
    dropped = 0
    if lines and lines[-1] == "":
        lines.pop()
    elif lines:
        tail = lines.pop()
        if tail.startswith(_FRAME_PREFIX):
            dropped += 1
        else:
            raise ValueError(f"unrecognized data at end of {path}: {tail[:40]!r}")

    frames = []
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        decoded = _decode_frame(line)
        if decoded is None:
            raise ValueError(f"invalid journal record at {path}:{lineno}: {line[:40]!r}")
        seq, payload, ok = decoded
        if not ok:
            dropped += 1
            continue
        frames.append((seq, payload))

    kept = []
    expect = None
    index = len(frames) - 1
    while index >= 0:
        seq, payload = frames[index]
        if expect is None or seq == expect:
            kept.append((seq, payload))
            expect = seq - 1
        elif seq == expect + 1:
            dropped += 1
        else:
            dropped += index + 1
            break
        index -= 1
    kept.reverse()

    last_seq = kept[-1][0] if kept else None
    return RecoveryResult([payload for _, payload in kept], dropped=dropped, last_seq=last_seq)
