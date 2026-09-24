"""自动助手：定制宠物的免费任务（永久）+ 定制次数池保底 50000 → 1000 收敛。

规则（2026-09-24 需求）：
- 定制宠物（`pet["custom"]`）勾选「修炼」（已婚自动改跑『双修』，同一个 key）或
  「幻境寻宝」时，执行**不扣次数**、剩余次数为 0 也不停机，与月卡/限时免费窗口无关；
  同一次扫描里排在它前面的收费任务额度不够时只跳过该任务，不能连带饿死免费任务。
- 非定制宠物一切照旧：额度归零即自动停机。
- 旧的 5 万次数池一次性向下收敛到 ASSISTANT_QUOTA_CUSTOM_PET（只降不升，
  低于新值的一律不动——那可能是玩家自己用自动助手卡充的）。

从父目录运行：cd C:\\Users\\18083\\Desktop\\bot && python -m pytest qqbot_pet/tests/test_assistant_custom_free.py -q
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'petbot_framework' / 'compat'))

from qqbot_pet.main import PetParkPlugin
from qqbot_pet.petpark import data
from qqbot_pet.petpark.pet import new_pet
from qqbot_pet.petpark.store import PetStore


def _plugin(store):
    plugin = PetParkPlugin.__new__(PetParkPlugin)
    plugin.store = store
    plugin.config = {}
    plugin._assistant_pending = {}
    return plugin


class AssistantCustomFreeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PetStore(Path(self.tmp.name) / 'petpark.json')
        self.plugin = _plugin(self.store)
        self.calls = []

        def fake_exec(key, player, pet, group_id, qq):
            self.calls.append(key)
            return f"[{key}] 执行完成"

        self.plugin._assistant_exec = fake_exec

        self.player = self.store.get_player('user', 'group')
        self.pet = new_pet('九尾狐', '超脱')
        self.pet['energy'] = 200
        self.player['pets'] = [self.pet]
        self.player['active_pet'] = 0
        self.store._flush()

    def use_pet(self, tasks, quota, custom=True, stage=None):
        self.pet['custom'] = custom
        if stage:
            self.pet['stage'] = stage
        self.pet['assistant'] = {
            'enabled': True, 'tasks': list(tasks), 'total_runs': 0,
            'last_run_at': 0, 'log': [],
        }
        self.player['assistant'] = {'quota': quota}
        return self.pet['assistant']

    async def tick(self):
        await self.plugin._assistant_tick()

    # ------------------------------------------------------------------
    # 免费任务：不扣次数、额度 0 也不停机
    # ------------------------------------------------------------------
    async def test_custom_pet_zero_quota_keeps_running_cultivation(self):
        a = self.use_pet(['修炼'], quota=0)
        await self.tick()
        self.assertEqual(self.calls, ['修炼'])
        self.assertTrue(a['enabled'], '定制宠物的免费任务不该因额度归零而停机')
        self.assertEqual(self.store.assistant_quota(self.player), 0)
        self.assertEqual(a['total_runs'], 1)
        self.assertEqual(len(a['log']), 1)

    async def test_custom_pet_free_task_does_not_consume_quota(self):
        a = self.use_pet(['修炼'], quota=5)
        await self.tick()
        self.assertEqual(self.calls, ['修炼'])
        self.assertEqual(self.store.assistant_quota(self.player), 5, '免费任务不得扣次数')
        self.assertTrue(a['enabled'])

    async def test_custom_pet_fantasy_treasure_is_free(self):
        a = self.use_pet(['幻境寻宝'], quota=0, stage='飞升')
        await self.tick()
        self.assertEqual(self.calls, ['幻境寻宝'])
        self.assertTrue(a['enabled'])
        self.assertEqual(self.store.assistant_quota(self.player), 0)

    async def test_married_custom_pet_dual_cultivation_is_free_too(self):
        # 「修炼」任务对已婚宠物内部改跑『双修』，仍是同一个 key，故同享免费
        self.pet['love_state'] = '已婚'
        a = self.use_pet(['修炼'], quota=0)
        await self.tick()
        self.assertEqual(self.calls, ['修炼'])
        self.assertTrue(a['enabled'])
        self.assertEqual(self.store.assistant_quota(self.player), 0)

    # ------------------------------------------------------------------
    # 收费任务：照旧扣次数、照旧停机；但不得连带饿死免费任务
    # ------------------------------------------------------------------
    async def test_custom_pet_paid_task_skipped_but_free_task_still_runs(self):
        a = self.use_pet(['打工', '修炼'], quota=0)
        await self.tick()
        self.assertEqual(self.calls, ['修炼'], '额度为 0 时收费任务应跳过，免费任务照跑')
        self.assertTrue(a['enabled'])
        # 收费任务仍被记录在可执行判定里，但不落日志
        self.assertEqual(a['total_runs'], 1)

    async def test_custom_pet_paid_task_still_costs_quota(self):
        a = self.use_pet(['打工'], quota=3)
        await self.tick()
        self.assertEqual(self.calls, ['打工'])
        self.assertEqual(self.store.assistant_quota(self.player), 2)
        self.assertTrue(a['enabled'])

    async def test_non_custom_pet_still_stops_at_zero_quota(self):
        a = self.use_pet(['修炼'], quota=0, custom=False)
        await self.tick()
        self.assertEqual(self.calls, [], '非定制宠物额度为 0 时不得执行，也不得被免费口径放行')
        self.assertFalse(a['enabled'], '非定制宠物额度归零应照旧自动停机')
        self.assertEqual(a['total_runs'], 0)

    async def test_non_custom_pet_normal_task_costs_quota(self):
        a = self.use_pet(['修炼'], quota=2, custom=False)
        await self.tick()
        self.assertEqual(self.calls, ['修炼'])
        self.assertEqual(self.store.assistant_quota(self.player), 1)
        self.assertTrue(a['enabled'])

    # ------------------------------------------------------------------
    # 开关门禁：定制宠物额度 0 也能开启
    # ------------------------------------------------------------------
    def test_toggle_allows_zero_quota_when_a_free_task_is_picked(self):
        self.use_pet(['修炼'], quota=0)
        self.pet['assistant']['enabled'] = False
        text = self.plugin._assistant_toggle(self.player, True)
        self.assertIn('已开启', text)
        self.assertTrue(self.pet['assistant']['enabled'])

    def test_toggle_still_blocks_zero_quota_without_free_task(self):
        self.use_pet(['打工'], quota=0)
        self.pet['assistant']['enabled'] = False
        text = self.plugin._assistant_toggle(self.player, True)
        self.assertIn('次数不足', text)
        self.assertFalse(self.pet['assistant']['enabled'])

    def test_toggle_blocks_non_custom_pet_at_zero_quota(self):
        self.use_pet(['修炼'], quota=0, custom=False)
        self.pet['assistant']['enabled'] = False
        text = self.plugin._assistant_toggle(self.player, True)
        self.assertIn('次数不足', text)
        self.assertFalse(self.pet['assistant']['enabled'])

    # ------------------------------------------------------------------
    # 次数池保底 50000 → 1000
    # ------------------------------------------------------------------
    def test_custom_quota_constant_is_1000(self):
        self.assertEqual(data.ASSISTANT_QUOTA_CUSTOM_PET, 1000)
        self.assertIn('1000', data.ITEMS['宠物定制卡']['desc'])

    def test_migration_caps_legacy_50000_for_custom_pet_owners_only(self):
        cap = data.ASSISTANT_QUOTA_CUSTOM_PET
        custom_rich = self.store.get_player('rich', 'group')
        custom_rich['pets'] = [dict(self.pet, custom=True)]
        custom_rich['assistant'] = {'quota': 50000}
        no_pet_rich = self.store.get_player('nopet', 'group')
        no_pet_rich['pets'] = [dict(self.pet, custom=False)]
        no_pet_rich['assistant'] = {'quota': 50000}
        custom_poor = self.store.get_player('poor', 'group')
        custom_poor['pets'] = [dict(self.pet, custom=True)]
        custom_poor['assistant'] = {'quota': 500}  # 自己充的卡，不能动

        self.store._data.pop('custom_quota_v2', None)
        self.store._migrate_custom_quota_v2()

        self.assertEqual(self.store.assistant_quota(custom_rich), cap)
        self.assertEqual(self.store.assistant_quota(no_pet_rich), 50000, '非定制宠物主人不该被收敛')
        self.assertEqual(self.store.assistant_quota(custom_poor), 500, '低于新值的一律不动')

    def test_migration_is_idempotent_and_does_not_eat_later_card_purchases(self):
        cap = data.ASSISTANT_QUOTA_CUSTOM_PET
        rich = self.store.get_player('rich', 'group')
        rich['pets'] = [dict(self.pet, custom=True)]
        rich['assistant'] = {'quota': 50000}
        self.store._data.pop('custom_quota_v2', None)
        self.store._migrate_custom_quota_v2()
        self.assertEqual(self.store.assistant_quota(rich), cap)

        # 迁移跑过之后再充卡充到 5 万以上，重跑迁移不得吞掉充值
        self.store.add_assistant_quota(rich, 60000)
        self.store._migrate_custom_quota_v2()
        self.assertEqual(self.store.assistant_quota(rich), cap + 60000)

    def test_raise_custom_quota_uses_new_value_and_never_lowers(self):
        poor = self.store.get_player('poor', 'group')
        self.assertEqual(self.store.raise_custom_assistant_quota(poor), 1000)
        poor['assistant']['quota'] = 7000
        self.assertEqual(self.store.raise_custom_assistant_quota(poor), 7000)


if __name__ == '__main__':
    unittest.main()
