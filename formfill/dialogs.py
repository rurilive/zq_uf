"""确认弹窗按类型处理，替代原来遇到弹窗就阻塞的行为。"""
from dataclasses import dataclass, field

CONFIRM = "confirm"
WARNING = "warning"
VALIDATION = "validation"
ERROR = "error"
UNKNOWN = "unknown"

# 确认/警告类弹窗：自动确认后重放一次提交。
AUTO_ACCEPT = frozenset({CONFIRM, WARNING})
# 校验/错误类弹窗：自动取消，不重放，失败原因连同字段写入报告。
AUTO_DISMISS = frozenset({VALIDATION, ERROR})


class DialogBlocked(Exception):
    """弹窗无法自动处理，需要人工介入。"""

    def __init__(self, dialog_type, message="", fields=None):
        self.dialog_type = dialog_type
        self.message = message
        self.fields = fields or []
        super().__init__(f"弹窗 {dialog_type} 需人工处理: {message}")


@dataclass
class DialogPolicy:
    accept_types: frozenset = field(default_factory=lambda: AUTO_ACCEPT)
    dismiss_types: frozenset = field(default_factory=lambda: AUTO_DISMISS)
    max_accept_rounds: int = 1
    # 自定义处理器：接收弹窗字典，返回 accept/dismiss/manual。
    handler: object = None

    def decide(self, dialog):
        dialog_type = dialog.get("type", UNKNOWN)
        if self.handler is not None:
            decision = self.handler(dialog)
            if decision in ("accept", "dismiss", "manual"):
                return decision, dialog_type
        if dialog_type in self.accept_types:
            return "accept", dialog_type
        if dialog_type in self.dismiss_types:
            return "dismiss", dialog_type
        return "manual", dialog_type


DEFAULT_POLICY = DialogPolicy()


def normalize_dialog(payload):
    """把 submit_one 抛出的弹窗信号统一成字典。"""
    if isinstance(payload, dict):
        return {
            "type": payload.get("type", UNKNOWN),
            "message": payload.get("message", ""),
            "fields": payload.get("fields", []),
        }
    return {"type": UNKNOWN, "message": str(payload), "fields": []}
