"""中元节《青灯伴萌宠 · 幽影饲育馆》活动 —— 独立模块。

本包把中元活动的全部代码集中在 ``petpark/zhongyuan/``，与宠物乐园主玩法解耦：
- 数据独立持久化到 ``zhongyuan.json``（不与 petpark.json 混在一起）；
- 后台提供「活动总开关 / 一键关闭全部玩法 / 一键删除活动代码」三控制项；
- 删除本目录并移除 main.py 里的接入钩子，即可整体下架活动，不影响主程序。

对外的接入点是 :class:`ZhongyuanActivity`：
- ``commands()``        —— 返回指令首词集合，供 main.py 放行路由；
- ``dispatch(...)``     —— 处理一条活动指令，返回回复文本或 None；
- ``start()``           —— 启动后台循环（每小时抽人 / 解密时限 / 结算）；
- ``terminate()``       —— 取消后台循环并落盘。
"""
from .engine import COMMANDS, ZhongyuanActivity

__all__ = ["ZhongyuanActivity", "COMMANDS"]
