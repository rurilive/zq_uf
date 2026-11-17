"""解析错误类型。"""


class UnknownReportFormatError(ValueError):
    """报告格式无法识别。

    原始报告保留在 ``raw`` 字段里，调用方可以留档或回退到人工查看，
    而不是让原文静默丢失。
    """

    def __init__(self, message, raw=None):
        super().__init__(message)
        self.raw = raw
