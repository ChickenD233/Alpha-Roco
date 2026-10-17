"""技能 handler 实现包（按类别分文件，逐个实现中）。

约定与 sim/traits/impl 一致：每个文件内自注册，``battle.py`` 侧只需导入本包。

简单技能用声明式写法（效果原语）::

    from ..base import SkillHandler          # 需要自定义钩子时
    from ..ops import OpsSkill, GainEnergy, ApplyBuff, When, IsCounter
    from ..registry import register

    class Scratch(OpsSkill):
        skill_id = 7020360
        name = "抓挠"
        category = 0
        implemented = True
        hit_effects = (GainEnergy(value=1),)

    register(Scratch())

复杂技能（条件分支/跨技能状态/巧变/位置）直接继承 ``SkillHandler`` 覆写钩子。

按类别分文件推进：
  attack.py   攻击类（category 0/1，345 条）
  defense.py  防御类（category 2，52 条）
  status.py   状态类（category 3，157 条）

实现完一个技能后，把 ``data/skills.json`` 中该条的 ``done`` 改为 ``true``
（唯一进度依据，与特性同约定）；``skills.check()`` 核对代码定义与
skills.json 的 name/category 是否一致。
"""

from __future__ import annotations

from . import defense  # noqa: F401  防御类（category 2，52 条已完成减伤/应对类别）
from . import attack  # noqa: F401  攻击类（category 0/1）
from . import status  # noqa: F401  状态类（category 3）
from . import wish  # noqa: F401  愿力冲击（特性替换版，skill_id=-2）
