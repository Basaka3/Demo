"""归还业务的起步接口。完成后可原样交给公共运行器调用。"""
from runtime.engine import ServiceNotReady


def resolve(question, borrow_branch, return_branch, ranked):
    """根据问题、结构化馆点信息与真实模型召回候选组织归还答复。

    ranked 的每项含 id、title、text、score，按分数从高到低排列。
    返回 decision、return_to、missing_fields、sources、content。
    可另提供保留的 ranked 条目，运行器会使用配置中的上限与阈值。
    """
    raise ServiceNotReady("请按0302题要求实现归还咨询")
