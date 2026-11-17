"""reproenv 的异常类型。

约定：

* 报告本身不合法（格式、依赖声明不可钉死等）抛 :class:`ReportError`
  （``ValueError`` 子类），调用方可以在 build 之前就拦下来；
* 依赖拉取/解析失败抛 :class:`DepResolveError`，携带包名与版本，
  build 捕获后停在准备阶段并把包名版本写进日志；
* 自定义 runner 想把某一步判失败时抛 :class:`StepError`，可以带
  ``actual`` 与 ``detail``，core 会统一记成该步骤失败。
"""


class ReportError(ValueError):
    """报告内容不合法。"""


class DepResolveError(Exception):
    """依赖拉取失败，携带包名与版本，供准备阶段停机并记日志。"""

    def __init__(self, name, version="", reason="依赖拉取失败", spec=None):
        self.name = name
        self.version = version
        self.reason = reason
        if spec is None:
            spec = "%s==%s" % (name, version) if version else str(name)
        self.spec = spec
        super().__init__("%s: %s" % (reason, self.spec))


class StepError(Exception):
    """自定义 runner 主动抛出的步骤失败。"""

    def __init__(self, detail, actual=None, abort=False):
        self.detail = detail
        self.actual = actual
        self.abort = abort
        super().__init__(detail)
