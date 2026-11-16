"""回归结果留存。

- MemoryStore：默认，进程内保留全部历史轮次；
- FileStore：落盘留存，按 run 分文件 + 原子写，进程重启后历史仍可查可对比。

每轮留存的是自包含快照（规则、用例、逐条结果），
不依赖当前内存里的用例集，后续 report/compare 直接读快照即可。
"""
import json
import os
import tempfile
from typing import List, Optional


class MemoryStore:
    """进程内留存。"""

    def __init__(self):
        self._runs = []
        self._counter = 0

    def next_run_id(self) -> int:
        self._counter += 1
        return self._counter

    def save_run(self, record: dict) -> None:
        self._runs.append(record)

    def get_run(self, run_id: int) -> Optional[dict]:
        for record in self._runs:
            if record["run_id"] == run_id:
                return record
        return None

    def list_runs(self) -> List[dict]:
        return [self._index_entry(record) for record in self._runs]

    def latest(self) -> Optional[dict]:
        return self._runs[-1] if self._runs else None

    def clear(self) -> None:
        self._runs.clear()
        self._counter = 0

    @staticmethod
    def _index_entry(record: dict) -> dict:
        return {
            "run_id": record["run_id"],
            "timestamp": record["timestamp"],
            "biz": record.get("biz"),
            "only_affected": record.get("only_affected", False),
            "summary": record["summary"],
        }


class FileStore(MemoryStore):
    """落盘留存：目录下 index.json 记索引，runs/run_<id>.json 存快照。"""

    INDEX_NAME = "index.json"
    RUNS_DIR = "runs"

    def __init__(self, path: str):
        if not isinstance(path, str) or not path:
            raise ValueError("store path must be a non-empty string")
        self.path = os.path.abspath(path)
        self._runs_dir = os.path.join(self.path, self.RUNS_DIR)
        os.makedirs(self._runs_dir, exist_ok=True)
        super().__init__()
        self._load_existing()

    def _load_existing(self):
        index_path = os.path.join(self.path, self.INDEX_NAME)
        if not os.path.isfile(index_path):
            return
        with open(index_path, "r", encoding="utf-8") as fh:
            index = json.load(fh)
        for entry in index.get("runs", []):
            run_file = os.path.join(
                self._runs_dir, f"run_{entry['run_id']}.json"
            )
            with open(run_file, "r", encoding="utf-8") as fh:
                record = json.load(fh)
            self._runs.append(record)
            self._counter = max(self._counter, record["run_id"])

    def save_run(self, record: dict) -> None:
        super().save_run(record)
        run_file = os.path.join(self._runs_dir, f"run_{record['run_id']}.json")
        self._atomic_write_json(run_file, record)
        index = {"runs": [self._index_entry(item) for item in self._runs]}
        self._atomic_write_json(os.path.join(self.path, self.INDEX_NAME), index)

    def clear(self) -> None:
        super().clear()
        for name in os.listdir(self._runs_dir):
            if name.endswith(".json"):
                os.remove(os.path.join(self._runs_dir, name))
        index_path = os.path.join(self.path, self.INDEX_NAME)
        if os.path.isfile(index_path):
            os.remove(index_path)

    @staticmethod
    def _atomic_write_json(path: str, payload) -> None:
        directory = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
