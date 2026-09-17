"""`projection` 线性口径回归（v3.10.18 去除 log2 压缩）。

去 log2 的前提是「灵宠/坐骑原始属性不设上限、养成投入按原值等比兑现」。这里钉死三条：
- 线性：投影值与原值成正比（不再有对数尾部压平）；
- 单调：原值越大投影越大（重生 Lv1 灵宠的成长方向不变）；
- 负值归零：脏数据不产出负属性。
外加一条量级校验：灵宠实战战力与「我的宠物」面板值同量级——去 log2 前两者实测差 2.7 亿倍，
误用面板值会把修士本体压到总战力的 0.00%（见 `petpark/adventure/power.py` 模块说明）。
"""
import math
import unittest

from qqbot_pet.petpark.adventure import power
from qqbot_pet.petpark.adventure.combat import companion_sheet, projection


class ProjectionTests(unittest.TestCase):
    def test_linear_in_raw_value(self):
        # 攻击档 base=50 weight=12：原值 10 倍 → 投影 10 倍，无压缩。
        self.assertEqual(projection(500, 50, 12), 120.0)
        self.assertEqual(projection(5_000, 50, 12), projection(500, 50, 12) * 10)

    def test_tail_not_flattened(self):
        """去 log2 的动机：原值涨 5866 万倍，旧对数口径只换约 17 倍。"""
        old = lambda v: 12 * math.log2(1 + v / 50)  # noqa: E731 已删除的旧实现
        self.assertLess(old(100 * 58_660_000) / old(100), 20)
        self.assertEqual(projection(100 * 58_660_000, 50, 12) / projection(100, 50, 12),
                         58_660_000)

    def test_monotonic_and_clamped(self):
        vals = [projection(v, 50, 12) for v in (0, 1, 50, 500, 5_000, 5_000_000)]
        self.assertEqual(vals, sorted(vals))
        self.assertEqual(projection(-999, 50, 12), 0.0)

    def test_pet_power_same_order_as_panel_value(self):
        """战力只能走战斗同源的 companion_sheet：两口径同量级、且都随生命上限线性爬升。"""
        pet = {"hp_max": 5_000_000, "atk": 300_000, "def": 200_000, "intel": 100_000}
        panel = pet["hp_max"]  # battle_power = 生命上限 × 心情（心情∈[0.8,1.2]）
        sheet_power = power.pet_power(pet)
        self.assertLess(max(panel, sheet_power) / min(panel, sheet_power), 100)
        self.assertGreater(power.pet_power({**pet, "hp_max": pet["hp_max"] * 10}),
                           sheet_power * 2)
        self.assertEqual(companion_sheet(pet, power.MAX_LEVEL)["hp"] > 0, True)


if __name__ == "__main__":
    unittest.main()
