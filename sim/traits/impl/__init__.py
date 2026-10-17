"""特性 handler 实现包（按类别分文件，逐个实现中）。

每个文件内：
  from ..base import TraitHandler
  from ..registry import register

  class Xxx(TraitHandler):
      trait_id = 200122
      name = "加个雪球"
      desc = "..."
      implemented = True
      def on_xxx(self, ctx): ...

  register(Xxx())

battle.py 已预留 ``from .traits import impl`` 导入点，实现文件注册后自动生效。
"""

from __future__ import annotations

from . import entry  # noqa: F401  入场/离场类
from . import skill  # noqa: F401  技能使用类
from . import round_end  # noqa: F401  回合结束类
from . import kill  # noqa: F401  击败/力竭类
from . import power  # noqa: F401  第 1 批：威力修正
from . import stats  # noqa: F401  第 1 批：属性/速度修正
from . import energy  # noqa: F401  第 1 批：能耗/能量修正
from . import taken  # noqa: F401  第 1 批：受击修正
from . import hit_count  # noqa: F401  第 1 批：连击修正
from . import lifesteal  # noqa: F401  第 1 批：吸血修正
from . import restrict  # noqa: F401  第 1 批：技能位限制
from . import freeze  # noqa: F401  冰冻增益类（buff_gain）
from . import lethal  # noqa: F401  致命伤害保护（on_lethal）
from . import buff_gain  # noqa: F401  增益响应类（on_buff_gain）
from . import resource  # noqa: F401  资源转移类（on_heal / on_energy_gain）
from . import counter  # noqa: F401  应对类（on_counter）
from . import morph  # noqa: F401  巧变类（morph_pool 填充）
from . import star  # noqa: F401  星陨印记相关（modify_star_consume）
