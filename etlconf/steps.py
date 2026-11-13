"""转换步骤的原语：空值判定、类型转换、派生表达式。"""
import ast
import operator


class StepError(ValueError):
    """单行数据在某个转换步骤中失败。"""


def is_null(value, null_tokens):
    """空值仅指 None 或声明的空值标记；0 / "0" / 0.0 是零值，不算空。"""
    if value is None:
        return True
    return isinstance(value, str) and value in null_tokens


def cast_value(value, type_name):
    if type_name == "str":
        return str(value)
    if type_name == "float":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise StepError("无法把 {!r} 转成 float".format(value)) from exc
    if type_name == "int":
        return _to_int(value)
    raise StepError("未知类型 {!r}".format(type_name))


def _to_int(value):
    if isinstance(value, bool):
        raise StepError("无法把 {!r} 转成 int".format(value))
    if isinstance(value, int):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StepError("无法把 {!r} 转成 int".format(value)) from exc
    if not number.is_integer():
        raise StepError("{!r} 不是整数，拒绝截断".format(value))
    return int(number)


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UAdd, ast.USub,
)


def compile_expression(source, field):
    """加载期解析派生表达式，语法或节点非法即抛 PlanError。"""
    from .core import PlanError  # 延迟导入，避免循环依赖
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise PlanError("派生字段 {!r} 表达式非法: {!r}".format(field, source)) from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise PlanError(
                "派生字段 {!r} 表达式含不支持的语法: {!r}".format(field, source))
    return tree


def eval_expression(tree, row):
    try:
        return _eval_node(tree.body, row)
    except TypeError as exc:
        raise StepError("派生表达式求值失败: {}".format(exc)) from exc


def _eval_node(node, row):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise StepError("表达式常量只允许数字: {!r}".format(node.value))
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in row:
            raise StepError("派生表达式引用了不存在的字段 {!r}".format(node.id))
        return row[node.id]
    if isinstance(node, ast.BinOp):
        return _BIN_OPS[type(node.op)](_eval_node(node.left, row), _eval_node(node.right, row))
    if isinstance(node, ast.UnaryOp):
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand, row))
    raise StepError("不支持的表达式节点: {!r}".format(node))
