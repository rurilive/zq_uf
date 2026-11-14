"""演示：大附件分片上传，中途断连后续传，最后清单对账。"""
import io
import json
import os
import tempfile

from attacharchive import reconcile, upload

CHUNK = 64 * 1024


class FlakyStream:
    """读到 cut 字节后模拟断连。"""

    def __init__(self, data, cut):
        self._data = data
        self._cut = cut
        self._pos = 0

    def read(self, size=-1):
        if self._pos >= self._cut:
            raise ConnectionError("网络中断")
        end = self._cut if size < 0 else min(self._pos + size, self._cut)
        piece = self._data[self._pos:end]
        self._pos = end
        return piece


def main():
    root = tempfile.mkdtemp(prefix="attacharchive-")
    data = bytes(range(256)) * 4096  # 1 MiB

    try:
        upload("big.bin", FlakyStream(data, 200 * 1024), root, chunk_size=CHUNK)
    except ConnectionError as exc:
        print("上传中断:", exc)

    result = upload("big.bin", io.BytesIO(data), root, chunk_size=CHUNK)
    print("断点续传: 分片数=%d 续传=%d 重传=%d"
          % (result["parts"], result["resumed"], result["retransmitted"]))

    again = upload("big.bin", io.BytesIO(data), root, chunk_size=CHUNK)
    print("幂等完成: status=%s 重传=%d" % (again["status"], again["retransmitted"]))

    with open(os.path.join(root, "manifest.json"), encoding="utf-8") as handle:
        stored = json.load(handle)
    entries = [dict(key=k, **v) for k, v in stored["files"].items()]
    print("对账:", reconcile(entries, root))


if __name__ == "__main__":
    main()
