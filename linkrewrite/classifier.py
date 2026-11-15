"""查询参数分类：追踪参数、分页参数、普通参数。

分类结果是重写引擎隔离处理追踪参数与分页参数的依据，站点可以通过
``Registry.configure_site`` 扩展各自的参数表。
"""

# 已知追踪参数：广告投放、来源统计类
DEFAULT_TRACKING = frozenset(
    [
        "gclid",
        "dclid",
        "fbclid",
        "spm",
        "scm",
        "from",
        "isappinstalled",
        "mc_eid",
        "mc_cid",
        "vero_id",
        "yclid",
    ]
)

# 已知分页参数：翻页、偏移、页大小类
DEFAULT_PAGINATION = frozenset(
    [
        "page",
        "p",
        "pn",
        "pageno",
        "page_no",
        "page_num",
        "offset",
        "limit",
        "start",
        "size",
        "per_page",
        "page_size",
        "cursor",
    ]
)

TRACKING = "tracking"
PAGINATION = "pagination"
OTHER = "other"
CLASSES = (TRACKING, PAGINATION, OTHER)


def classify_param(name, tracking_extra=None, pagination_extra=None):
    """返回参数名所属类别。

    判定顺序：追踪参数优先于分页参数（同一名字同时出现在两张表时按追踪处理），
    其余为普通参数。``utm_`` 前缀的参数一律视为追踪参数。
    """
    tracking = set(DEFAULT_TRACKING)
    pagination = set(DEFAULT_PAGINATION)
    if tracking_extra:
        tracking.update(tracking_extra)
    if pagination_extra:
        pagination.update(pagination_extra)

    if name in tracking or name.startswith("utm_"):
        return TRACKING
    if name in pagination:
        return PAGINATION
    return OTHER
