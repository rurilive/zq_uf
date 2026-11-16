"""reproenv 的异常类型。

输入类错误（报告格式不对、环境不存在）抛 ValueError 子类；
依赖拉取失败用 DepResolveError，便于自定义 resolver 复用同一套语义。
"""


class ReportError(ValueError):
    """报告内容不合法。"""


class DepResolveError(Exception):
    """依赖拉取/解析失败，携带包名与版本，准备阶段据此停机并记日志。"""

    def __init__(self, name, version="", reason="依赖拉取失败", spec=None):
        self.name = name
        self.version = version
        self.reason = reason
        self.spec = spec or (("%s==%s" % (name, version)) if version else str(name))
        super().__init__("%s: %s" % (reason, self.spec))


class StepError(Exception):
    """自定义 runner 主动抛出的步骤失败。"""

    def __init__(self, detail, actual=None):
        self.detail = detail
        self.actual = actual
        super().__init__(detail)
