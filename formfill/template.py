"""模板声明：业务键、日期与金额的转换规则。"""
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN
from zoneinfo import ZoneInfo

_ROUNDING = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
}


class TemplateError(ValueError):
    """模板声明本身不合法。"""


class FieldFormatError(ValueError):
    """单个字段的值无法按模板转换，携带字段名与原值。"""

    def __init__(self, field_name, value, reason):
        self.field_name = field_name
        self.value = value
        self.reason = reason
        super().__init__(f"字段 {field_name} 值 {value!r} 转换失败: {reason}")


@dataclass(frozen=True)
class Template:
    # 业务键取自哪些列，单列即原值；多列按顺序拼成 "a:b:c"。
    key_fields: tuple = ("id",)
    date_input_formats: tuple = ("iso", "%Y-%m-%d")
    date_output_format: str = "iso"  # iso 或 strftime 模板
    amount_precision: int = 2
    amount_rounding: str = "half_up"  # 与旧版 round(float, 2) 的常见预期一致，可配 half_even
    amount_as_decimal: bool = False
    timezone: str = "UTC"  # 时间戳/日期时间转换所用时区
    field_map: dict = field(default_factory=dict)  # 标准字段 -> 模板里的列名

    def __post_init__(self):
        if not self.key_fields:
            raise TemplateError("key_fields 不能为空")
        if self.amount_precision < 0:
            raise TemplateError("amount_precision 不能为负")
        if self.amount_rounding not in _ROUNDING:
            raise TemplateError(f"不支持的舍入方式: {self.amount_rounding}")
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:  # pragma: no cover - 非法时区
            raise TemplateError(f"非法时区 {self.timezone!r}: {exc}") from exc

    @property
    def tzinfo(self):
        return ZoneInfo(self.timezone)

    def with_overrides(self, **overrides):
        return replace(self, **overrides)

    def business_key(self, row):
        parts = []
        for name in self.key_fields:
            if name not in row or row[name] in (None, ""):
                raise FieldFormatError(name, row.get(name), "业务键字段缺失或为空")
            parts.append(str(row[name]))
        return ":".join(parts)

    def _column(self, standard_name):
        return self.field_map.get(standard_name, standard_name)

    def convert_date(self, value):
        if value in (None, ""):
            raise FieldFormatError("date", value, "日期为空")
        if isinstance(value, datetime):
            parsed = value.astimezone(self.tzinfo) if value.tzinfo else value.replace(tzinfo=self.tzinfo)
            parsed_date = parsed.date()
        elif isinstance(value, date):
            parsed = value
            parsed_date = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(float(value), tz=self.tzinfo)
            parsed_date = parsed.date()
        else:
            text_value = str(value).strip()
            parsed = None
            for fmt in self.date_input_formats:
                try:
                    if fmt == "iso":
                        parsed = date.fromisoformat(text_value)
                    else:
                        parsed = datetime.strptime(text_value, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise FieldFormatError(
                    "date", value, f"不匹配任何输入格式 {self.date_input_formats}"
                )
            parsed_date = parsed.date() if isinstance(parsed, datetime) else parsed
        if self.date_output_format == "iso":
            return parsed_date.isoformat()
        if isinstance(parsed, datetime):
            return parsed.strftime(self.date_output_format)
        return parsed_date.strftime(self.date_output_format)

    def convert_amount(self, value):
        if value in (None, ""):
            raise FieldFormatError("amount", value, "金额为空")
        try:
            if isinstance(value, Decimal):
                decimal_value = value
            elif isinstance(value, bool):
                raise ValueError("布尔值不是合法金额")
            else:
                decimal_value = Decimal(str(value).strip().replace(",", ""))
        except (ValueError, ArithmeticError):
            raise FieldFormatError("amount", value, "不是合法金额") from None
        if not decimal_value.is_finite():
            raise FieldFormatError("amount", value, "金额必须是有限数")
        quant = Decimal(1).scaleb(-self.amount_precision)
        rounded = decimal_value.quantize(quant, rounding=_ROUNDING[self.amount_rounding])
        return rounded if self.amount_as_decimal else float(rounded)

    def normalize(self, row):
        """按模板把原始行转成统一载荷，原值保留在 _raw 便于报告。"""
        normalized = {"_raw": dict(row)}
        normalized["key"] = self.business_key(row)
        normalized["date"] = self.convert_date(row[self._column("date")])
        normalized["amount"] = self.convert_amount(row[self._column("amount")])
        normalized["note"] = row.get(self._column("note"), "")
        return normalized


DEFAULT_TEMPLATE = Template()


def normalize_row(row, template=None):
    """向后兼容：默认模板下的行规范化。"""
    return (template or DEFAULT_TEMPLATE).normalize(row)
