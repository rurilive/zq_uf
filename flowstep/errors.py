"""flowstep 的异常类型与业务错误码。"""

# 引擎在步骤执行前后产生的标准错误码；业务步骤也可通过 fail() 自定义错误码。
INPUT_MISSING = "INPUT_MISSING"
OUTPUT_MISSING = "OUTPUT_MISSING"
STEP_FAILED = "STEP_FAILED"
STEP_RAISED = "STEP_RAISED"
BAD_RESULT = "BAD_RESULT"


class StepError(Exception):
    """业务步骤主动抛出的可定位错误。"""

    def __init__(self, code=STEP_FAILED, field=None, message=""):
        self.code = code
        self.field = field
        self.message = message or code
        super().__init__(message or code)


class FlowDefinitionError(ValueError):
    """流程定义本身不合法（重名、处理器缺失、条件非法等）。"""


def fail(code=STEP_FAILED, field=None, message=""):
    """在步骤处理器中调用，中断流程并返回业务错误码与字段名。"""
    raise StepError(code, field, message)
