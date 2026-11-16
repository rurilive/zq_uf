"""报告解析相关异常。"""


class ReportParseError(ValueError):
    """报告格式无法识别或结构不完整。

    ``report`` 保留报告原文（字符串）或原始对象，便于人工回查，
    避免解析失败时原始信息丢失。
    """

    def __init__(self, message, report=None):
        super().__init__(message)
        self.message = message
        self.report = report
