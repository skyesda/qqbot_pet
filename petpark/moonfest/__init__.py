"""「月耀华诞」中秋 × 国庆双阶段活动 —— 独立模块。

本包把月耀华诞活动的全部代码集中在 ``petpark/moonfest/``，与灵契仙途主玩法解耦：

- 数据独立持久化到 ``moonfest.json``（不与 petpark.json 混在一起）；
- 「月华」是活动唯一奖品与排名积分。**累计**月华只进不出（``yuehua_earned`` 单调
  递增，榜/群里程碑/结算读它）；唯一花销出口是「月华商店」的上限次数卡，它只写
  独立的 ``yuehua_spent``（可用余额 = earned − spent），不减累计；
- 玩法/里程碑/活动结束排行榜奖励全部只写回本模块的 ``players`` 桶，不碰主玩法背包；
- 活动结束（中秋与国庆两阶段 ``end_at`` 均已过）后**统一结算一次**排行榜奖励，
  由 ``meta.settled`` 幂等保护；
- 删除本目录并移除 main.py 里的接入钩子，即可整体下架活动，不影响主程序。

对外接入点是 :class:`MoonfestActivity`：

- ``commands()``  —— 返回指令首词集合，供 main.py 放行路由；
- ``dispatch(...)`` —— 处理一条活动指令，返回回复文本或 None；
- ``start()``     —— 启动后台循环（阶段切换 / 活动结束自动结算 / 落盘）；
- ``terminate()`` —— 取消后台循环并落盘。
"""
from .engine import COMMANDS, MoonfestActivity

__all__ = ["MoonfestActivity", "COMMANDS"]
