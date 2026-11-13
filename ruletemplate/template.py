"""质量规则应用，当前每个数据集自带完整规则。"""


def apply(dataset, rules):
    """原样返回传入规则，没有模板和覆盖。"""
    return {
        "dataset": dataset,
        "rules": [dict(rule) for rule in rules],
    }
