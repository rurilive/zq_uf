"""内容指纹规则集：追踪参数、参数顺序、锚点与路径规范化。"""
from dataclasses import dataclass, replace


# 常见投放/统计参数；只移除明确不影响内容的参数，`ref`、`source` 等
# 仍可能参与业务逻辑，故意保留。
DEFAULT_TRACKING_PARAMS = frozenset({
    "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "fbclid", "msclkid",
    "yclid", "twclid", "ttclid", "igshid", "mc_cid", "mc_eid", "mkt_tok",
    "vero_id", "vero_conv", "_hsenc", "_hsmi", "hsctatracking", "__hstc",
    "__hssc", "__hsfp", "spm", "scm", "sc_cid", "trk", "trkcampaign",
    "oly_anon_id", "oly_enc_id", "ga_source", "ga_medium", "ga_campaign",
    "ga_term", "ga_content", "_ga", "_gl", "ref_src", "ref_url", "ncid",
    "cmpid", "campaignid", "campaign_id", "ck_subscriber_id", "s_kwcid",
    "ef_id", "_openstat",
})

DEFAULT_TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_", "matomo_")

# 分页参数即使出现在查询串里也要保留，分页指向的是不同内容。
DEFAULT_PAGINATION_PARAMS = frozenset({
    "page", "p", "pg", "paged", "offset", "limit", "per_page", "page_size",
    "pagesize", "cursor", "start", "after", "before", "continue",
})


@dataclass(frozen=True)
class Ruleset:
    """一次可版本化的判定规则。

    分页参数不会被移除，因此不同页仍得到不同指纹；追踪参数无论出现在
    查询串的哪个位置都会被丢弃。
    """

    version: str = "content-v1"
    tracking_params: frozenset = DEFAULT_TRACKING_PARAMS
    tracking_prefixes: tuple = DEFAULT_TRACKING_PREFIXES
    pagination_params: frozenset = DEFAULT_PAGINATION_PARAMS
    drop_fragment: bool = True
    sort_query: bool = True
    normalize_path: bool = True
    strip_trailing_slash: bool = True

    def is_tracking(self, name):
        """参数名不区分大小写；分页参数优先于追踪前缀，永不移除。"""
        lowered = (name or "").lower()
        if lowered in self.pagination_params:
            return False
        if lowered in self.tracking_params:
            return True
        return any(lowered.startswith(prefix) for prefix in self.tracking_prefixes)

    def evolve(self, **changes):
        return replace(self, **changes)


DEFAULT_RULESET = Ruleset()
