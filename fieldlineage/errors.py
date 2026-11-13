"""血缘模块的异常类型，所有失败路径都显式抛出，不静默跳过。"""


class LineageError(Exception):
    """血缘模块所有异常的基类。"""


class JobFormatError(LineageError, ValueError):
    """record(job) 的任务格式非法：缺字段、类型错误、引用未声明的表等。"""


class SQLParseError(LineageError, ValueError):
    """SQL 无法解析或不支持该语法（select *、子查询、UNION 等）。"""


class UnknownFieldError(LineageError, ValueError):
    """trace_field 查询的字段不存在于血缘图中。"""
