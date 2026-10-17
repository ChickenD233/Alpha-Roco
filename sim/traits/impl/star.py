"""星陨印记相关特性。

- 200210 守望星  触发星陨印记时仅消耗一半层数，仍造成满层伤害。

接入说明：引擎星陨段（battle.py 印记7分支）伤害按触发时满层计算不变；
消耗层数经 traits.query_star_consume 查询（默认全部消耗），由
marks.consume_mark_stacks 执行部分消耗。
"""

from __future__ import annotations

from ..base import TraitHandler
from ..registry import register


# ---------------- 200210 守望星 ----------------
class StarWatcher(TraitHandler):
    trait_id = 200210
    name = "守望星"
    desc = "触发星陨印记时仅消耗一半层数，仍造成满层伤害。"
    implemented = True

    def modify_star_consume(self, ctx, stacks):
        # 自己触发星陨印记（攻击带星陨印记的敌方）时仅消耗一半：
        # 保留层数向下取整 floor(n/2)，消耗 n - floor(n/2)（1 层时正常消耗完）
        if ctx.target is not ctx.actor:
            return None
        return stacks - stacks // 2


def register_star() -> None:
    register(StarWatcher())


register_star()
