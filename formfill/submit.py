"""并发且幂等的批量填报。

设计口径：
- 每条行先按模板算业务键，提交回执按业务键落本地存储；
- 重试先核对回执：成功跳过、失败重放、状态不明转待人工，必要时远端核对；
- 同一业务键由 KeyCoordinator 全局串行化，冲突调用阻塞进等待队列；
- 单条失败只记录不拖垮整批，最终产出成功/跳过/失败/待人工四份清单。
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .coordinator import KeyCoordinator
from .dialogs import (
    DEFAULT_POLICY,
    DialogBlocked,
    UNKNOWN,
    normalize_dialog,
)
from .receipt import (
    FAILED,
    MANUAL,
    SKIPPED,
    SUCCESS,
    Receipt,
    ReceiptStore,
    classify_status,
    reconcile,
)
from .template import DEFAULT_TEMPLATE, FieldFormatError


_DEFAULT_STORE = None
_DEFAULT_COORDINATOR = None


def _default_store():
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = ReceiptStore()
    return _DEFAULT_STORE


def _default_coordinator():
    global _DEFAULT_COORDINATOR
    if _DEFAULT_COORDINATOR is None:
        _DEFAULT_COORDINATOR = KeyCoordinator()
    return _DEFAULT_COORDINATOR


def reset_defaults():
    """清空进程级默认存储与协调器，主要给测试用。"""
    global _DEFAULT_STORE, _DEFAULT_COORDINATOR
    _DEFAULT_STORE = None
    _DEFAULT_COORDINATOR = None


@dataclass
class BatchResult:
    success: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    manual: list = field(default_factory=list)
    preview: list = field(default_factory=list)
    dry_run: bool = False
    order: list = field(default_factory=list)  # 输入顺序的业务键

    def __iter__(self):
        buckets = (self.success, self.skipped, self.failed, self.manual, self.preview)
        by_key = {item.business_key: item for bucket in buckets for item in bucket}
        for key in self.order:
            if key in by_key:
                yield by_key[key]

    def __len__(self):
        return len(self.order)

    def __getitem__(self, index):
        return self.receipts[index]

    @property
    def receipts(self):
        return list(self)

    def counts(self):
        return {
            "success": len(self.success),
            "skipped": len(self.skipped),
            "failed": len(self.failed),
            "manual": len(self.manual),
            "preview": len(self.preview),
        }

    def report(self):
        """可直接序列化的批次报告，字段错误含字段名与原值。"""
        return {
            "dry_run": self.dry_run,
            "counts": self.counts(),
            "success": [item.to_dict() for item in self.success],
            "skipped": [item.to_dict() for item in self.skipped],
            "failed": [item.to_dict() for item in self.failed],
            "manual": [item.to_dict() for item in self.manual],
            "preview": [item.to_dict() for item in self.preview],
        }


def _default_submit_one(payload):
    return {"status": "ok"}


def _field_errors(error, raw_row):
    return [{
        "field": error.field_name,
        "value": raw_row.get(error.field_name, error.value),
        "reason": error.reason,
    }]


def _fallback_key(row, template):
    """规范化失败时尽力还原业务键，保证报告里能对上原始行。"""
    try:
        return template.business_key(row)
    except FieldFormatError:
        parts = [str(row.get(name, "")) or "?" for name in template.key_fields]
        return ":".join(parts) if parts else str(row.get("id", "?"))


def _dialog_fields(dialog, raw_row):
    fields = []
    for entry in dialog.get("fields", []) or []:
        item = dict(entry)
        item.setdefault("value", raw_row.get(item.get("field", ""), ""))
        fields.append(item)
    return fields


def _run_one(row, submit_one, template, store, coordinator, policy, verifier):
    try:
        payload = template.normalize(row)
    except FieldFormatError as error:
        receipt = Receipt(
            business_key=_fallback_key(row, template),
            status="validation_error",
            message=str(error),
            category=FAILED,
            fields=_field_errors(error, row),
        )
        store.save(receipt)
        return FAILED, receipt

    key = payload["key"]
    with coordinator.lease(key):
        prior, action = reconcile(store, key, verifier)
        if action == "skip":
            skipped = Receipt(
                business_key=key,
                status=prior.status,
                message=prior.message or "已有成功回执，跳过",
                category=SKIPPED,
                receipt_id=prior.receipt_id,
                dialog=prior.dialog,
            )
            return SKIPPED, skipped
        if action == "manual":
            return MANUAL, prior

        attempt_payload = dict(payload)
        accept_rounds = 0
        while True:
            try:
                result = submit_one(attempt_payload) or {}
            except DialogBlocked as blocked:
                dialog = normalize_dialog({
                    "type": blocked.dialog_type,
                    "message": blocked.message,
                    "fields": blocked.fields,
                })
            except (TimeoutError, ConnectionError, OSError) as error:
                receipt = Receipt(
                    business_key=key,
                    status="timeout",
                    message=f"传输层异常，不自动重试: {error}",
                    category=MANUAL,
                )
                store.save(receipt)
                return MANUAL, receipt
            except Exception as error:  # 单条失败隔离，不拖垮整批
                receipt = Receipt(
                    business_key=key,
                    status="failed",
                    message=str(error) or error.__class__.__name__,
                    category=FAILED,
                )
                store.save(receipt)
                return FAILED, receipt
            else:
                signal = result.get("dialog")
                if not signal and result.get("status") == "dialog":
                    signal = result
                dialog = normalize_dialog(signal) if signal else None

            if dialog:
                decision, dialog_type = policy.decide(dialog)
                if decision == "accept" and accept_rounds < policy.max_accept_rounds:
                    accept_rounds += 1
                    attempt_payload = dict(payload)
                    attempt_payload["_dialog_confirmed"] = dialog_type
                    continue
                if decision == "accept":
                    receipt = Receipt(
                        business_key=key,
                        status="manual",
                        message=f"弹窗 {dialog_type} 确认次数超过上限",
                        category=MANUAL,
                        dialog=dialog_type,
                        fields=_dialog_fields(dialog, payload["_raw"]),
                    )
                    store.save(receipt)
                    return MANUAL, receipt
                if decision == "dismiss":
                    receipt = Receipt(
                        business_key=key,
                        status="validation_error",
                        message=dialog.get("message", "弹窗被自动取消"),
                        category=FAILED,
                        dialog=dialog_type,
                        fields=_dialog_fields(dialog, payload["_raw"]),
                    )
                    store.save(receipt)
                    return FAILED, receipt
                receipt = Receipt(
                    business_key=key,
                    status="manual",
                    message=dialog.get("message", "未知弹窗，等待人工"),
                    category=MANUAL,
                    dialog=dialog_type or UNKNOWN,
                    fields=_dialog_fields(dialog, payload["_raw"]),
                )
                store.save(receipt)
                return MANUAL, receipt

            status = result.get("status", "ok")
            receipt = Receipt(
                business_key=key,
                status=status,
                message=result.get("message", ""),
                category=classify_status(status),
                receipt_id=result.get("receipt_id", ""),
                dialog=attempt_payload.get("_dialog_confirmed", ""),
            )
            store.save(receipt)
            return receipt.category, receipt


def _dry_run(rows, template, store):
    result = BatchResult(dry_run=True)
    for row in rows:
        try:
            payload = template.normalize(row)
        except FieldFormatError as error:
            key = _fallback_key(row, template)
            result.order.append(key)
            result.failed.append(Receipt(
                business_key=key,
                status="validation_error",
                message=str(error),
                category=FAILED,
                fields=_field_errors(error, row),
            ))
            continue
        key = payload["key"]
        result.order.append(key)
        prior = store.get(key)
        if prior is not None and prior.category == SUCCESS:
            result.skipped.append(Receipt(
                business_key=key,
                status=prior.status,
                message="干跑：命中已有成功回执，正式运行将跳过",
                category=SKIPPED,
            ))
            continue
        result.preview.append(Receipt(
            business_key=key,
            status="preview",
            message="干跑预览，不会提交",
        ))
    return result


def submit_batch(rows, workers=4, *,
                 submit_one=None, template=None, store=None,
                 coordinator=None, dialog_policy=None, verifier=None,
                 dry_run=False):
    """并发提交一批行，返回带四份清单的 BatchResult（仍可按输入顺序迭代回执）。

    兼容旧签名 submit_batch(rows, submit_one)：第二个位置参数为可调用对象时
    视为提交函数，workers 取默认值。
    """
    if callable(workers):
        submit_one = workers
        workers = 4
    submit_one = submit_one or _default_submit_one
    template = template or DEFAULT_TEMPLATE
    store = store or _default_store()
    coordinator = coordinator or _default_coordinator()
    dialog_policy = dialog_policy or DEFAULT_POLICY

    rows = list(rows)
    if dry_run:
        return _dry_run(rows, template, store)
    if not rows:
        return BatchResult()

    result = BatchResult()
    max_workers = max(1, min(int(workers), len(rows)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                _run_one, row, submit_one, template, store,
                coordinator, dialog_policy, verifier,
            )
            for row in rows
        ]
        outcomes = [future.result() for future in futures]

    buckets = {
        SUCCESS: result.success,
        SKIPPED: result.skipped,
        FAILED: result.failed,
        MANUAL: result.manual,
    }
    for category, receipt in outcomes:
        result.order.append(receipt.business_key)
        buckets.setdefault(category, result.manual).append(receipt)
    return result
