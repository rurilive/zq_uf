"""业务回执与本地已提交记录。"""
import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field

# 终态分类：成功可跳过；失败可安全重放；待人工需要介入。
SUCCESS = "success"
FAILED = "failed"
MANUAL = "manual"
SKIPPED = "skipped"

SUCCESS_STATUSES = frozenset({"ok", "success", "accepted", "confirmed"})
FAILED_STATUSES = frozenset({"rejected", "failed", "validation_error", "error"})
MANUAL_STATUSES = frozenset({"manual", "unknown", "timeout", "transient"})


def classify_status(status):
    if status in SUCCESS_STATUSES:
        return SUCCESS
    if status in MANUAL_STATUSES:
        return MANUAL
    if status in FAILED_STATUSES:
        return FAILED
    return MANUAL


@dataclass
class Receipt:
    business_key: str
    status: str
    message: str = ""
    category: str = ""
    receipt_id: str = ""
    fields: list = field(default_factory=list)  # [{field, value, reason}]
    dialog: str = ""

    def __post_init__(self):
        if not self.category:
            object.__setattr__(self, "category", classify_status(self.status))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(
            business_key=data["business_key"],
            status=data.get("status", ""),
            message=data.get("message", ""),
            category=data.get("category", ""),
            receipt_id=data.get("receipt_id", ""),
            fields=data.get("fields", []),
            dialog=data.get("dialog", ""),
        )


class ReceiptStore:
    """线程安全的业务键 -> 最新回执记录，可选 JSON 文件持久化。"""

    def __init__(self, path=None):
        self._lock = threading.RLock()
        self._records = {}
        self.path = path
        if path and os.path.exists(path):
            self._load()

    def _load(self):
        with open(self.path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self._records = {
            key: Receipt.from_dict(item) for key, item in payload.items()
        }

    def _flush_locked(self):
        if not self.path:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".receipts-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {key: item.to_dict() for key, item in self._records.items()},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp_path, self.path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def get(self, business_key):
        with self._lock:
            return self._records.get(business_key)

    def save(self, receipt):
        with self._lock:
            self._records[receipt.business_key] = receipt
            self._flush_locked()
            return receipt

    def all(self):
        with self._lock:
            return list(self._records.values())

    def reset(self):
        with self._lock:
            self._records.clear()
            if self.path and os.path.exists(self.path):
                os.unlink(self.path)


def reconcile(store, business_key, verifier=None):
    """重试前按本地回执核对。

    返回 (receipt, action)：
    - 成功记录 -> skip，直接跳过不重放；
    - 失败记录 -> replay，失败是明确终态，重放安全；
    - 待人工/未知记录 -> 先用 verifier 向远端核对，核对成功补登为成功，
      否则保持 manual 等待人工，避免超时盲重试开出重复单据；
    - 无记录 -> replay。
    """
    receipt = store.get(business_key)
    if receipt is None:
        return None, "replay"
    if receipt.category == SUCCESS:
        return receipt, "skip"
    if receipt.category == FAILED:
        return receipt, "replay"
    if verifier is not None:
        remote = verifier(business_key) or {}
        remote_status = remote.get("status", "")
        if classify_status(remote_status) == SUCCESS:
            confirmed = Receipt(
                business_key=business_key,
                status=remote_status,
                message=remote.get("message", "远端核对确认已提交"),
                category=SUCCESS,
                receipt_id=remote.get("receipt_id", receipt.receipt_id),
                dialog=receipt.dialog,
            )
            store.save(confirmed)
            return confirmed, "skip"
    return receipt, "manual"
