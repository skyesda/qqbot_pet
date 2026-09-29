"""月耀华诞扩充玩法契约：月饼重制挑战链 + 中秋/国庆新增玩法 + 月华信息。

这批测试锁的是**机制而不是文案**，五条重点：

1. 每日计数器「一处定义」不能再被绕过 —— 引擎里出现一个没登记进 ``_DAILY_KEYS``
   的计数器，测试立刻红（这个模块踩过三次：漏登记的后果不是报错，而是计数器一路
   累加、玩家第二天发现次数没恢复）；
2. Jev 不可用时**不出语义题**、判分也不放水（线上当前就没配 Jev Key，这是主路径）；
3. 月相推算的确定性与「近满窗口必须够宽」—— 窗口退回 1 天会让加成在中秋段空转，
   用 2026 年的真实望日把这个陷阱钉死；
4. 月华只进不出：任何失败路径都只吃冷却和次数，绝不扣月华；献礼点只进
   ``offering_total``，不许污染 ``yuehua_total``；
5. 「月华信息」只读：冷启动、任何阶段、任何指令组合下都不许抛异常、不许改状态。
"""
import copy
import datetime as dt
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

# moonfest 包的 __init__ 会导入 engine，engine 依赖框架的 astrbot；本地跑测试时用
# petbot_framework/compat 下的桩补上（test_moonfest_banks.py 也是这么做的）。
_COMPAT = Path(__file__).resolve().parents[2] / "petbot_framework" / "compat"
if _COMPAT.is_dir() and str(_COMPAT) not in sys.path:
    sys.path.insert(0, str(_COMPAT))

from qqbot_pet.petpark.moonfest import challenges, moonphase as MP, puzzles
from qqbot_pet.petpark.moonfest import templates as T
from qqbot_pet.petpark.moonfest.config import DEFAULT_CONFIG
from qqbot_pet.petpark.moonfest.engine import COMMANDS, MoonfestActivity, _DAILY_KEYS

_ENGINE_SRC = (Path(__file__).resolve().parents[1]
               / "petpark" / "moonfest" / "engine.py").read_text(encoding="utf-8")

BJ = ZoneInfo("Asia/Shanghai")


def bj_ts(y, m, d, h=0, mi=0) -> float:
    return dt.datetime(y, m, d, h, mi, tzinfo=BJ).timestamp()


class _FakeBot:
    """最小宿主桩：engine 只用到这几个方法。"""

    def _sender_name(self, event, qq=None):
        return "测试玩家"

    async def _send_group_text(self, group_id, text):
        return None

    def _broadcast_targets(self):
        return []

    def _broadcast_to_authorized_groups(self, text):
        return 0


class _EngineCase(unittest.TestCase):
    """起一个离线引擎：关掉 Jev（线上没 Key，兜底路径就是主路径）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.act = MoonfestActivity(_FakeBot(), Path(self.tmp.name))
        self.act.jev.enabled = False
        # 单元测试里没有事件循环，_spawn 留着只会刷 RuntimeWarning
        self.act._spawn = lambda coro: coro.close()
        self.phase = "midautumn"
        self.act._phase = lambda: self.phase
        self.gid, self.qq = "10001", "20001"

    def say(self, text):
        return self.act.dispatch(None, self.qq, self.gid, text) or ""

    def ap(self):
        """本玩家档（不存在则建，省得每条测试都先 _get_player 一次）。"""
        return self.act._get_player(self.gid, self.qq)

    def earned(self):
        return int(self.ap().get("yuehua_earned", 0) or 0)

    def mats(self):
        return self.act._materials(self.ap())


# ---------------------------------------------------------------------------
# 一、每日计数器的唯一真源
# ---------------------------------------------------------------------------
class DailyKeyTests(_EngineCase):
    #: 引擎里 ``d`` 也用来指「每日开放时段」那个**配置** dict，这两个键不是计数器
    _CFG_DAILY_KEYS = {"open_hour", "close_hour"}

    def test_engine_never_uses_an_unregistered_daily_counter(self):
        used = set(re.findall(r'\bd(?:\.get\(|\[)"([a-z_]+)"', _ENGINE_SRC))
        used -= {"date"} | self._CFG_DAILY_KEYS
        self.assertEqual(used - set(_DAILY_KEYS), set(),
                         "引擎用了没登记进 _DAILY_KEYS 的每日计数器")

    def test_new_daily_covers_exactly_the_registry(self):
        from qqbot_pet.petpark.moonfest.engine import _new_daily

        self.assertEqual(set(_new_daily()) - {"date"}, set(_DAILY_KEYS))
        # 历史键 bake 仍要保留：旧档里有它，删掉会让补旧档少一个键
        self.assertIn("bake", _DAILY_KEYS)

    def test_reset_zeroes_every_registry_key(self):
        ap = self.ap()
        for k in _DAILY_KEYS:
            ap["daily"][k] = 7
        ap["daily"]["date"] = "2000-01-01"      # 逼出跨天重置
        self.act._daily_reset(ap)
        for k in _DAILY_KEYS:
            self.assertEqual(ap["daily"][k], 0, f"{k} 没有被每日重置")
        self.assertEqual(ap["daily"]["date"], self.act._bj_date())

    def test_old_save_is_backfilled(self):
        """旧档（缺新键）读出来必须补齐，否则计数走 .get(k,0) 永远写不回档。"""
        ap = self.ap()
        for k in list(_DAILY_KEYS):
            ap["daily"].pop(k, None)
        for key in ("brew", "rabbit", "route", "materials", "double"):
            ap.pop(key, None)
        fresh = self.ap()
        for k in _DAILY_KEYS:
            self.assertIn(k, fresh["daily"])
        for key in ("brew", "rabbit", "route", "materials", "double"):
            self.assertIn(key, fresh, f"旧档没有补出 {key}")


# ---------------------------------------------------------------------------
# 二、月饼重制挑战题库
# ---------------------------------------------------------------------------
class ChallengeBankTests(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(20260929)

    def _steps(self, stage, count=3, jev=False, n=40):
        out = []
        for _ in range(n):
            out.extend(challenges.build_steps(0, stage, count, jev_available=jev,
                                              rng=self.rng))
        return out

    def test_every_step_is_answerable_as_stored(self):
        """拿题库里的答案原文去作答，必须判对（判分器不许把正解判错）。"""
        for stage in (1, 2, 3, 4, 5):
            for step in self._steps(stage):
                self.assertNotEqual(step["kind"], "semantic", "Jev 不可用时不该出语义题")
                self.assertTrue(challenges.is_correct(step, str(step["a"])),
                                f"照答案作答被判错：{step!r}")
                for key in ("kind", "q", "a", "answer_type", "hint"):
                    self.assertTrue(step.get(key), f"题目缺字段 {key}: {step!r}")

    def test_order_question_answer_is_really_the_next_step(self):
        """工序排序题的唯一正解必须是「紧接着的下一步」，不能是任意一道工序。"""
        steps = [s for s in self._steps(3, count=4) if s["kind"] == "order"]
        self.assertTrue(steps, "没有生成到工序排序题")
        seq = challenges._CRAFT_STEPS
        for s in steps:
            m = re.search(r"「(.+?)」之后紧接着", s["q"])
            self.assertIsNotNone(m, f"题面读不出被问的工序：{s['q']}")
            self.assertEqual(seq[seq.index(m.group(1)) + 1], s["a"],
                             f"正解不是紧接着的下一步：{s['q']} → {s['a']}")

    def test_phase_question_has_an_unambiguous_answer(self):
        """月相推演题的答案必须离相界足够远 —— 压在相界上就没有正确答案。"""
        nominal = dict(challenges._PHASE_STARTS)
        steps = [s for s in self._steps(4, count=4) if s["kind"] == "phase"]
        self.assertTrue(steps, "没有生成到月相推演题")
        for s in steps:
            m = re.search(r"今夜是(\S+?)（月龄约", s["q"])
            n = re.search(r"再过 (\d+) 天", s["q"])
            self.assertIsNotNone(m, f"题面读不出起算月相：{s['q']}")
            self.assertIsNotNone(n, f"题面读不出推演天数：{s['q']}")
            self.assertIn(m.group(1), nominal, f"起算月相不在素材表里：{m.group(1)}")
            age = (nominal[m.group(1)] + int(n.group(1))) % MP.SYNODIC_MONTH
            self.assertGreaterEqual(MP.phase_distance_to_edge(age), 0.5,
                                    f"答案落在相界上，无正解：{s['q']} → {s['a']}")
            self.assertEqual(MP.phase_name_for_age(age), s["a"])

    def test_higher_stage_means_more_and_faster(self):
        """阶段越高：题数不减、限时不增、单次月华递增。"""
        counts = DEFAULT_CONFIG["craft_remake_counts"]
        times = DEFAULT_CONFIG["craft_remake_times"]
        rewards = DEFAULT_CONFIG["craft_remake_rewards"]
        self.assertEqual(len(counts), len(times))
        self.assertEqual(len(counts), len(rewards))
        self.assertEqual(counts, sorted(counts), "阶段变高题数不该减少")
        self.assertEqual(times, sorted(times, reverse=True), "阶段变高限时不该变长")
        self.assertEqual(rewards, sorted(rewards), "阶段变高月华不该减少")
        self.assertGreater(counts[-1], counts[0], "最高阶段必须比入门明显更重")
        self.assertLess(times[-1], times[0], "最高阶段必须比入门更赶")
        self.assertGreater(rewards[-1], rewards[0] * 3, "最高阶段收益要拉得开")

    def test_stage_pools_never_shrink(self):
        pools = [set(challenges.kinds_for_stage(s)) for s in range(1, 6)]
        for i in range(1, len(pools)):
            self.assertTrue(pools[i - 1] <= pools[i],
                            f"阶段 {i + 1} 的题型池比上一阶段少了")

    def test_semantic_steps_never_appear_when_jev_is_unavailable(self):
        """**核心放水防线**：Jev 不可用时不许出语义题，且题数一根不少。"""
        for stage in (4, 5):
            for count in (1, 3, 4):
                steps = challenges.build_steps(0, stage, count, jev_available=False,
                                               rng=self.rng)
                self.assertEqual(len(steps), count, "补足步数失败")
                self.assertNotIn("semantic", [s["kind"] for s in steps])

    def test_semantic_slot_appears_only_when_allowed(self):
        steps = challenges.build_steps(0, 5, 4, jev_available=True, rng=self.rng)
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[-1]["kind"], "semantic")
        # 阶段 3 不到最低阶段，即便 Jev 可用也不出语义题
        s3 = challenges.build_steps(0, 3, 4, jev_available=True, rng=self.rng)
        self.assertNotIn("semantic", [s["kind"] for s in s3])

    def test_semantic_grading_never_passes_without_jev(self):
        step = challenges.make_semantic(self.rng, 5)
        self.assertFalse(challenges.grade(step, "我不知道", jev=None)[0])
        self.assertTrue(challenges.grade(step, step["pass_keys"][0], jev=None)[0])


# ---------------------------------------------------------------------------
# 三、月相：确定性与「窗口不许太窄」
# ---------------------------------------------------------------------------
class MoonPhaseTests(unittest.TestCase):
    def test_full_moons_land_on_the_expected_dates(self):
        got = {}
        for t in MP.full_moon_timestamps(bj_ts(2026, 1, 1), bj_ts(2027, 1, 1)):
            d = dt.datetime.fromtimestamp(t, BJ)
            got[(d.year, d.month, d.day)] = t
        self.assertIn((2026, 9, 26), got, f"实际望日：{sorted(got)}")
        self.assertIn((2026, 10, 26), got, f"实际望日：{sorted(got)}")
        times = [got[k] for k in sorted(got)]
        for a, b in zip(times, times[1:]):
            self.assertAlmostEqual((b - a) / 86400.0, MP.SYNODIC_MONTH, delta=0.01)

    def test_phase_name_at_the_full_moon_is_full(self):
        for t in MP.full_moon_timestamps(bj_ts(2026, 9, 20), bj_ts(2026, 10, 1)):
            self.assertEqual(MP.phase_name(t), "满月")
            self.assertLessEqual(MP.days_from_full_moon(t), 0.001)
            self.assertTrue(MP.is_near_full(t, 3))

    def test_phase_name_by_age_walks_the_cycle_in_order(self):
        span = MP.SYNODIC_MONTH / 8.0
        self.assertEqual([MP.phase_name_for_age(span * i) for i in range(8)],
                         MP.PHASES)

    def test_near_full_window_actually_covers_the_midautumn_phase(self):
        """近满窗口必须在**中秋段内至少盖 3 天**。

        2026 年的望在 09-26 深夜，而中秋段是 09-27 才开始 —— 窗口只给 1 天的话
        整段活动只有 09-27 命中、之后这个加成就是死的。把这条钉死，防止有人把
        full_moon_window_days 调回 1。
        """
        cfg = DEFAULT_CONFIG
        window = int(cfg["full_moon_window_days"])
        self.assertGreaterEqual(window, 3, "近满窗口小于 3 天会在中秋段空转")
        hit = [d for d in range(27, 31) if MP.is_near_full(bj_ts(2026, 9, d), window)]
        self.assertGreaterEqual(len(hit), 3, f"中秋段近满日只有 {hit} 天")
        narrow = [d for d in range(27, 31) if MP.is_near_full(bj_ts(2026, 9, d), 1)]
        self.assertLessEqual(len(narrow), 1, "1 天窗口本该只命中 1 天")
        self.assertLess(cfg["phase_midautumn"]["start_at"],
                        cfg["phase_midautumn"]["end_at"])


# ---------------------------------------------------------------------------
# 四、月饼重制：难度递增 / 失败代价 / 一次性奖励幂等
# ---------------------------------------------------------------------------
class CraftRemakeTests(_EngineCase):
    def _answer_active(self):
        act = self.ap()["craft"].get("active")
        self.assertIsNotNone(act, "没有开起来的挑战")
        return self.say("重制 " + str(act["steps"][act["i"]]["a"]))

    def test_first_craft_unlocks_then_remake_requires_challenge(self):
        self.assertIn("首次解锁", self.say("做月饼 五仁"))
        self.assertEqual(self.ap()["craft"]["mooncake"]["五仁"], 1)
        self.assertIn("重制开始", self.say("做月饼 五仁"))
        self.assertIsNotNone(self.ap()["craft"]["active"])
        # 挑战开着时再开一炉会被挡住，而不是把当前那炉顶掉
        self.assertIn("还有一次重制挑战", self.say("做月饼 五仁"))

    def test_unknown_flavor_is_rejected(self):
        self.assertIn("没有", self.say("做月饼 榴莲"))

    def test_stage_is_driven_by_this_players_own_remake_count(self):
        """阶段只看**该玩家自己**的成功次数：后来者不被前置玩家拖累。"""
        self.assertEqual(self.act._craft_stage(self.ap()), 1)   # 0 次 → 入门
        for count, stage in ((0, 1), (1, 1), (2, 2), (5, 3), (9, 4), (14, 5), (99, 5)):
            ap = {"craft": {"remake_count": count}}
            self.assertEqual(self.act._craft_stage(ap), stage, f"{count} 次该是 {stage} 阶")

    def test_cooldown_grows_with_stage_and_is_capped(self):
        cd = [self.act._craft_cooldown_secs(s) for s in range(1, 6)]
        self.assertEqual(cd, sorted(cd), "阶段越高冷却不该更短")
        self.assertLess(cd[0], cd[-1])
        cap = self.act._int_cfg("craft_remake_cooldown_cap_min", 60) * 60
        self.assertLessEqual(max(cd), cap)
        # 后台把步长改到极端值也不许突破上限
        self.act.cfg["craft_remake_cooldown_step_min"] = 999
        self.assertLessEqual(self.act._craft_cooldown_secs(5), cap)

    def test_wrong_answer_costs_a_try_and_writes_cooldown(self):
        self.say("做月饼 五仁")            # 首次解锁
        self.say("做月饼 五仁")            # 开局
        self.assertEqual(int(self.ap()["daily"]["craft_try"]), 1)
        before = self.earned()
        self.assertIn("作废", self.say("重制 绝不可能是答案的内容"))
        self.assertIsNone(self.ap()["craft"].get("active"))
        self.assertGreater(int(self.ap()["craft"].get("last_ts", 0)), 0)
        # 铁律：失败**不扣月华**，只是拿不到
        self.assertEqual(self.earned(), before)

    def test_daily_limit_blocks_further_attempts(self):
        limit = self.act._int_cfg("craft_remake_daily_limit", 3)
        self.say("做月饼 五仁")
        for _ in range(limit + 2):
            self.ap()["craft"]["last_ts"] = 0      # 清冷却，只测次数上限
            self.say("做月饼 五仁")
            if self.ap()["craft"].get("active"):
                self.say("重制 放弃")
        self.assertEqual(int(self.ap()["daily"]["craft_try"]), limit)
        self.assertIn("次数已用完", self.say("做月饼 五仁"))

    def test_abandon_costs_a_try_and_cannot_bypass_cooldown(self):
        self.say("做月饼 五仁")
        self.say("做月饼 五仁")
        self.assertIn("放弃", self.say("重制 放弃"))
        self.assertIsNone(self.ap()["craft"].get("active"))
        self.assertIn("歇口气", self.say("做月饼 五仁"))

    def test_correct_answers_advance_then_pay_the_stage_reward(self):
        self.say("做月饼 五仁")
        self.say("做月饼 五仁")
        ap = self.ap()
        act = ap["craft"]["active"]
        self.assertEqual(len(act["steps"]), 1, "入门阶段该只有 1 题")
        before = self.earned()
        out = self._answer_active()
        self.assertIn("重制成功", out)
        self.assertEqual(self.earned() - before,
                         DEFAULT_CONFIG["craft_remake_rewards"][0])
        self.assertEqual(ap["craft"]["remake_count"], 1)
        self.assertEqual(ap["craft"]["remake"]["五仁"], 1)
        self.assertEqual(ap["craft"]["mooncake"]["五仁"], 2)

    def test_star_bonus_is_granted_once_per_flavor_and_star(self):
        """星级里程碑靠 stars_granted 去重：封顶后重复通关不许反复触发同一档。"""
        ap = self.ap()
        rewards = DEFAULT_CONFIG["craft_star_rewards"]          # {"3": 60, "5": 120}
        stage3 = DEFAULT_CONFIG["craft_remake_rewards"][2]
        stage4 = DEFAULT_CONFIG["craft_remake_rewards"][3]

        def force_success(stage, n_steps):
            craft = self.act._craft_state(ap)
            act = {"flavor": "五仁", "steps": [{"a": "x"}] * n_steps, "i": n_steps - 1}
            before = self.earned()
            text = self.act._craft_success(ap, self.gid, craft, act, stage,
                                           self.act._now())
            return text, self.earned() - before

        # 前两次到 ★★，没有里程碑
        for _ in range(2):
            text, gained = force_success(3, 3)
            self.assertEqual(gained, stage3)
            self.assertNotIn("额外获得", text)
        # 第三次到 ★★★ → 发一次 3 星奖
        text, gained = force_success(3, 3)
        self.assertEqual(ap["craft"]["remake"]["五仁"], 3)
        self.assertEqual(gained, stage3 + int(rewards["3"]))
        self.assertIn("额外获得", text)
        # 第四次到 ★★★★ → 3 星奖不再发
        text, gained = force_success(4, 4)
        self.assertEqual(gained, stage4)
        self.assertNotIn("额外获得", text)
        self.assertIn("五仁:3", ap["craft"]["stars_granted"])


# ---------------------------------------------------------------------------
# 五、中秋新增：桂花酿 / 玉兔同行 / 灯谜做深
# ---------------------------------------------------------------------------
class BrewTests(_EngineCase):
    def test_brew_needs_materials_and_timer(self):
        self.assertIn("只有", self.say("酿桂花"))        # BREW_NO_GUIHUA
        self.mats()["桂花"] = 5
        self.assertIn("封坛下料", self.say("酿桂花"))    # BREW_START
        self.assertIn("酒还没酿好", self.say("取酒"))
        self.ap()["brew"]["ready_ts"] = self.act._now() - 1
        out = self.say("取酒")
        self.assertTrue(any(g in out for g in T.BREW_GRADES), out)
        self.assertEqual(int(self.ap()["brew"]["brewed"]), 1)

    def test_materials_are_consumed_exactly_once_per_batch(self):
        need = self.act._int_cfg("brew_guihua_per_batch", 3)
        self.mats()["桂花"] = need
        self.say("酿桂花")
        self.assertEqual(int(self.mats()["桂花"]), 0)
        # 还在酿 → 挡住第二次，材料没有被重复扣
        self.assertIn("还有一坛在酿着", self.say("酿桂花"))
        self.assertEqual(int(self.mats()["桂花"]), 0)
        # 清掉在酿状态后，材料不足就明确拒绝
        self.ap()["brew"]["ready_ts"] = 0
        self.ap()["daily"]["brew"] = 0
        self.assertIn("只有 0 份", self.say("酿桂花"))

    def test_daily_limit_caps_brewing(self):
        limit = self.act._int_cfg("brew_daily_limit", 1)
        self.mats()["桂花"] = 30
        for _ in range(3):
            self.say("酿桂花")
            self.ap()["brew"]["ready_ts"] = 0
        self.assertEqual(int(self.ap()["daily"]["brew"]), limit)
        self.assertIn("今日已起过", self.say("酿桂花"))

    def test_grade_bonus_is_granted_once_per_grade(self):
        bonuses = DEFAULT_CONFIG["brew_grade_bonus"]     # [20, 40, 80]
        for i, grade in enumerate(T.BREW_GRADES):
            self.act._brew_grade = (lambda g: (lambda now: g))(grade)
            for nth in (1, 2):
                self.mats()["桂花"] = 9
                self.ap()["daily"]["brew"] = 0
                self.say("酿桂花")
                self.ap()["brew"]["ready_ts"] = self.act._now() - 1
                before = self.earned()
                self.say("取酒")
                expect = int(bonuses[i]) if nth == 1 else 0
                self.assertEqual(self.earned() - before, expect,
                                 f"【{grade}】第 {nth} 次的奖励不对")
        self.assertEqual(sorted(self.ap()["brew"]["grades"]), sorted(T.BREW_GRADES))

    def test_wine_feeds_the_rabbit_with_a_tier_floor(self):
        self.ap()["brew"]["box"] = {"酿王": 1}
        before = self.earned()
        out = self.say("喂玉兔 桂花酿")
        self.assertIn("添进食盆", out)
        self.assertEqual(int(self.ap()["brew"]["box"]["酿王"]), 0, "喂了酒却没扣酒窖")
        # 酿王保底「非常喜欢」档（线上没 Jev，兜底档是 1，被保底抬到 3）
        self.assertEqual(self.earned() - before, int(DEFAULT_CONFIG["gongde_feed"][3]))

    def test_feeding_without_wine_is_refused_without_side_effects(self):
        out = self.say("喂玉兔 桂花酿")
        self.assertIn("酒窖里还没有存货", out)
        self.assertEqual(int(self.ap()["daily"]["feed"]), 0)
        self.assertEqual(self.earned(), 0)


class RabbitRunTests(_EngineCase):
    def _walk(self, answer_correctly=True):
        out = self.say("玉兔同行")
        for _ in range(12):
            q = self.ap().get("rabbit", {}).get("q")
            if not q:
                break
            out += self.say("玉兔同行 " + (str(q["a"]) if answer_correctly else "错错错"))
        return out

    def test_perfect_run_pays_step_rewards_plus_bonus(self):
        out = self._walk()
        self.assertIn("五站全过", out)
        bonus = self.act._int_cfg("rabbit_run_perfect_bonus", 25)
        self.assertEqual(self.ap()["rabbit"]["perfect_days"], 1)
        self.assertIn("×{}】".format(bonus), out)
        self.assertGreaterEqual(self.earned(), bonus, "完美奖励没有真发")
        self.assertIn("今日已经陪玉兔走过一趟", self.say("玉兔同行"))

    def test_fail_budget_ends_the_run_without_paying_the_bonus(self):
        fail_max = self.act._int_cfg("rabbit_run_fail_max", 2)
        out = self._walk(answer_correctly=False)
        self.assertIn("玉兔累了", out)
        self.assertNotIn("五站全过", out)
        rab = self.ap()["rabbit"]
        self.assertEqual(rab["perfect_days"], 0)
        self.assertIsNone(rab["q"])
        self.assertEqual(int(rab["wrong"]), fail_max)
        self.assertEqual(int(rab["earned"]), 0)
        self.assertEqual(self.earned(), 0)

    def test_later_stations_drop_the_hint(self):
        first = self.say("玉兔同行")
        self.assertIn("💡 提示", first)
        self.assertFalse(self.ap()["rabbit"]["q"]["hard"], "第 1 站不该用难题")
        for _ in range(2):                      # 走完第 1、2 站
            q = self.ap()["rabbit"]["q"]
            self.say("玉兔同行 " + str(q["a"]))
        q3 = self.ap()["rabbit"]["q"]
        self.assertTrue(q3["hard"], "第 3 站起该换难题池")
        self.assertNotIn("💡 提示", self.act._rabbit_ask(self.ap(), 60))

    def test_intimacy_multiplier_grows_with_feed_total(self):
        ap = self.ap()
        step = self.act._int_cfg("rabbit_intimacy_step", 10)
        ap["feed_total"] = 0
        self.assertEqual(self.act._rabbit_intimacy_mult(ap), (1.0, 0))
        ap["feed_total"] = step
        self.assertEqual(self.act._rabbit_intimacy_mult(ap)[1], 1)
        ap["feed_total"] = 10 ** 6
        self.assertEqual(self.act._rabbit_intimacy_mult(ap)[1], 2, "亲密度等级必须封顶")


class LanternDeepeningTests(_EngineCase):
    def test_hard_pool_is_a_real_strict_subset_and_big_enough(self):
        pool = puzzles.hard_lantern_pool()
        self.assertGreaterEqual(len(pool), 100, "难题池太小，每日 5 道会频繁重样")
        self.assertLess(len(pool), len(puzzles._LANTERNS), "难题池不该等于全量题库")
        for p in pool:
            self.assertTrue(puzzles._is_hard_lantern(p), f"混进了非难题：{p['question']}")
        self.assertLess(self.act._int_cfg("lantern_hard_daily_limit", 5), len(pool))

    def test_hard_daily_limit_is_separate_from_the_normal_one(self):
        hlimit = self.act._int_cfg("lantern_hard_daily_limit", 5)
        self.say("猜灯谜 难题")
        self.assertEqual(int(self.ap()["daily"]["lantern_hard"]), 1)
        for _ in range(hlimit + 2):
            self.ap()["quiz"] = {}          # 丢掉未答的题，逼它出新题
            self.say("猜灯谜 难题")
        self.assertEqual(int(self.ap()["daily"]["lantern_hard"]), hlimit)
        self.assertIn("难题次数已用完", self.say("猜灯谜 难题"))
        # 普通档不受难题上限影响
        self.ap()["quiz"] = {}
        self.assertNotIn("难题次数已用完", self.say("猜灯谜"))

    def test_hard_tier_pays_more_and_says_so(self):
        self.act._rand_int = lambda *a, **k: 10
        self.say("猜灯谜 难题")
        q = self.ap()["quiz"]
        self.assertTrue(q["hard"])
        before = self.earned()
        out = self.say("猜灯谜 " + str(q["a"]))
        mult = float(self.act._cfg("lantern_hard_mult", 1.5))
        self.assertEqual(self.earned() - before, int(10 * mult))
        self.assertIn("难题加成", out)

    def test_combo_multiplier_kicks_in_at_five_in_a_row(self):
        self.act._rand_int = lambda *a, **k: 10
        payouts = []
        for _ in range(5):
            self.say("猜灯谜")
            q = self.ap()["quiz"]
            before = self.earned()
            self.say("猜灯谜 " + str(q["a"]))
            payouts.append(self.earned() - before)
        self.assertEqual(self.ap()["lantern_streak"], 5)
        rate = float(self.act._cfg("lantern_combo_rate", 0.1))
        self.assertEqual(payouts[:4], [10] * 4, "连对不足 5 题不该有加成")
        self.assertEqual(payouts[4], int(10 * (1 + rate)))
        # 答错清零：再来一题又是原价
        self.say("猜灯谜")
        self.say("猜灯谜 绝不可能对的答案")
        self.assertEqual(int(self.ap()["lantern_streak"]), 0)

    def test_answering_the_stored_answer_is_never_rejected(self):
        """回归：题库答案本身若不是同义组代表写法，曾出现「照答案打都被判错」。

        线上真实发生过：题库存「孔明灯」，而它在同义表里归到代表写法「心愿灯」，
        normalize_answer 走「答案包含在输入里」那条捷径时没做归一、右边却归一了，
        于是玩家一字不差地打「孔明灯」被判错。
        """
        for answer in ("孔明灯", "望", "朔", "上弦", "盈凸", "中秋月饼", "天上"):
            self.assertTrue(
                puzzles.is_correct(answer, {"answer": answer, "answer_type": "str"}),
                f"照答案原文作答「{answer}」被判错")
        # 全库自检：任何一道题拿自己的答案原文作答都必须判对
        for p in puzzles._LANTERNS:
            self.assertTrue(puzzles.is_correct(str(p["answer"]), p),
                            f"灯谜自判失败：{p['question']} → {p['answer']}")
        for p in puzzles._QUIZ:
            self.assertTrue(puzzles.is_correct(str(p["answer"]), p),
                            f"巡礼自判失败：{p['q']} → {p['answer']}")


# ---------------------------------------------------------------------------
# 六、国庆新增：巡礼路线 / 献礼 / 双庆 / 贺词主题
# ---------------------------------------------------------------------------
class QuizRouteTests(_EngineCase):
    def setUp(self):
        super().setUp()
        self.phase = "national"

    def test_stations_escalate_and_reset_after_completion(self):
        n = self.act._int_cfg("route_stations", 5)
        complete = self.act._int_cfg("route_complete_bonus", 40)
        self.say("巡礼")
        seen, last = [], ""
        for _ in range(n + 2):
            q = self.ap().get("quiz") or {}
            self.assertEqual(q.get("kind"), "xunli", f"第 {len(seen) + 1} 站没抽到题")
            seen.append(int(q["station"]))
            last = self.say("巡礼 " + str(q["a"]))
            if int(q["station"]) >= n:
                break
            self.say("巡礼")        # 答对一站后要再发一次指令才抽下一站
        self.assertEqual(seen, list(range(1, n + 1)))
        self.assertIn("走完", last)
        self.assertIn(str(complete), last)
        self.assertEqual(int(self.ap()["route"]["station"]), 0)

    def test_difficulty_never_decreases_along_a_route(self):
        n = self.act._int_cfg("route_stations", 5)
        ap = self.ap()
        for base in (1, 2, 3):
            self.act._quiz_difficulty = (lambda b: (lambda ap_: b))(base)
            diffs = [self.act._route_difficulty(ap, s, n) for s in range(1, n + 1)]
            self.assertEqual(diffs, sorted(diffs), f"起点 {base} 时难度会回落")
            self.assertEqual(diffs[-1], 3, "末站必须是困难")

    def test_wrong_answers_reset_the_route_after_the_budget(self):
        fail_max = self.act._int_cfg("route_fail_max", 2)
        self.say("巡礼")
        self.say("巡礼 " + str(self.ap()["quiz"]["a"]))    # 先前进一站
        self.assertEqual(int(self.ap()["route"]["station"]), 1)
        for _ in range(fail_max - 1):
            self.say("巡礼")
            self.say("巡礼 完全错误的答案")
            self.assertEqual(int(self.ap()["route"]["station"]), 1, "答错不该推进")
        self.say("巡礼")
        self.assertIn("重置", self.say("巡礼 完全错误的答案"))
        self.assertEqual(int(self.ap()["route"]["station"]), 0)
        self.assertEqual(int(self.ap()["route"]["wrong"]), 0)

    def test_reroute_resets_progress_and_keeps_a_different_name(self):
        self.say("巡礼")
        self.say("巡礼 " + str(self.ap()["quiz"]["a"]))
        self.assertEqual(int(self.ap()["route"]["station"]), 1)
        old = self.ap()["route"]["name"]
        self.assertIn("换了一条路线", self.say("巡礼 换线"))
        self.assertEqual(int(self.ap()["route"]["station"]), 0)
        self.assertNotEqual(self.ap()["route"]["name"], old)
        self.assertEqual(int(self.ap()["quiz_streak"]), 0)

    def test_no_active_quiz_gets_a_readable_message(self):
        self.assertIn("先发「巡礼」", self.say("巡礼 随便答一个"))


class OfferingTests(_EngineCase):
    def setUp(self):
        super().setUp()
        self.phase = "national"
        # 新建档的 daily["date"] 是空串，而每天第一条指令的 _daily_reset 会把它当
        # 跨天、把所有计数器清零。真实路径永远先跑过一条指令，这里等价地预置一下
        # （不能用真指令：签到会带进 15 月华，污染下面按档位等差数列的断言）。
        self.act._daily_reset(self.ap())

    def test_offering_points_never_touch_the_group_yuehua_axis(self):
        """献礼点只进 offering_total，不许污染 yuehua_total。

        两条轴一个看「参与行为」、一个看「产出」；混在一起的话，巡礼答题会顺带推进
        月华里程碑，两个体系的平衡一起崩。
        """
        before = int(self.act._group_state(self.gid)["yuehua_total"])
        for _ in range(20):
            self.act._check_offering(self.ap(), self.gid, 3)
        gs = self.act._group_state(self.gid)
        self.assertEqual(int(gs["offering_total"]), 60)
        self.assertEqual(int(gs["yuehua_total"]), before, "献礼点不该推进群累计月华")

    def test_ladder_pays_once_and_scopes_to_the_group(self):
        ladder = self.act.cfg["offering_ladder"]
        first = int(ladder[0]["threshold"])
        gift = int(ladder[0]["yuehua"])
        outsider = self.act._get_player("88888", "29999")
        self.act._check_offering(self.ap(), self.gid, first)
        self.assertEqual(list(self.act._group_state(self.gid)["offering_reached"]), [0])
        self.assertEqual(self.earned(), gift)
        # 同一档不许发第二次
        self.act._check_offering(self.ap(), self.gid, 1)
        self.assertEqual(self.earned(), gift, "同一档发了两次")
        self.assertEqual(list(self.act._group_state(self.gid)["offering_reached"]), [0])
        self.assertEqual(int(outsider["yuehua_earned"]), 0, "别的群的玩家不该被发奖")
        out = self.say("献礼")
        self.assertIn("{} 点".format(first + 1), out)
        self.assertIn("{} 点".format(ladder[1]["threshold"]), out)

    def test_query_reports_progress_and_my_contribution(self):
        self.act._check_offering(self.ap(), self.gid, 7)
        out = self.say("献礼")
        self.assertIn("7 点", out)
        self.assertIn("今日你贡献了 7 点", out)

    def test_quiz_and_firework_feed_the_offering_axis(self):
        self.say("巡礼")
        self.say("巡礼 " + str(self.ap()["quiz"]["a"]))
        self.assertGreaterEqual(int(self.act._group_state(self.gid)["offering_total"]),
                                self.act._int_cfg("offering_quiz_point", 1))
        self.say("贺词 祝祖国繁荣昌盛")
        self.assertGreaterEqual(int(self.act._group_state(self.gid)["offering_total"]),
                                self.act._int_cfg("offering_firework_point", 3))


class DoubleFestivalTests(_EngineCase):
    def _run(self, correct=True):
        out = self.say("双庆")
        for _ in range(8):
            q = self.ap().get("double", {}).get("q")
            if not q:
                break
            out += self.say("双庆 " + (str(q["a"]) if correct else "不可能对的答案"))
        return out

    def test_only_open_on_the_overlap_day(self):
        for phase in ("midautumn", "national"):
            self.phase = phase
            self.assertIn("只在双节同庆日", self.say("双庆"))
        self.phase = "both"
        self.assertIn("特别挑战", self.say("双庆"))

    def test_all_correct_pays_once_and_locks_the_day(self):
        self.phase = "both"
        before = self.earned()
        out = self._run()
        reward = self.act._int_cfg("double_festival_reward", 150)
        self.assertIn("五题全中", out)
        self.assertEqual(self.earned() - before, reward)
        self.assertIn("已经做过了", self.say("双庆"))

    def test_one_wrong_ends_it_and_blocks_a_retry(self):
        self.phase = "both"
        before = self.earned()
        out = self._run(correct=False)
        self.assertIn("到此为止", out)
        self.assertEqual(self.earned(), before)
        self.assertIn("已经做过了", self.say("双庆"))

    def test_questions_alternate_between_the_two_festivals(self):
        kinds = ["quiz" if self.act._double_question(i)["options"] else "lantern"
                 for i in range(1, 6)]
        self.assertEqual(kinds, ["lantern", "quiz", "lantern", "quiz", "lantern"])
        for i in range(1, 6):
            q = self.act._double_question(i)
            self.assertTrue(q["q"] and q["a"] and q["extra"], f"第 {i} 题不完整")


class FireworkThemeTests(_EngineCase):
    ON_THEME = {
        "写给祖国的一句话": "祝愿祖国繁荣昌盛",
        "写给家人的一句话": "愿全家平安健康",
        "写给你思念的人": "远方的人我很想你",
        "写一句中秋团圆祝福": "中秋团圆月亮圆圆",
        "写一句家国同庆的祝福": "家国同庆双节快乐",
    }

    def setUp(self):
        super().setUp()
        self.phase = "national"

    def test_theme_is_stable_and_comes_from_the_config_list(self):
        themes = self.act.cfg["firework_themes"]
        self.assertIn(self.act._today_theme(), themes)
        self.assertEqual(self.act._today_theme(), self.act._today_theme())
        self.assertEqual(sorted(self.ON_THEME), sorted(themes),
                         "主题表变了，用例里的契合例句要跟着改")

    def test_on_theme_gets_bonus_and_off_theme_does_not(self):
        self.act._rand_int = lambda *a, **k: 5
        theme = self.act._today_theme()
        bonus = self.act._int_cfg("firework_theme_bonus", 10)
        before = self.earned()
        out = self.say("贺词 " + self.ON_THEME[theme])
        self.assertIn("紧扣今日主题", out)
        self.assertEqual(self.earned() - before, 5 + bonus)
        before = self.earned()
        out = self.say("贺词 今天天气不错随便说说")
        self.assertNotIn("紧扣今日主题", out)
        self.assertEqual(self.earned() - before, 5)

    def test_keyword_table_never_claims_a_fit_it_cannot_justify(self):
        from qqbot_pet.petpark.moonfest.engine import _theme_keyword_hit

        self.assertTrue(_theme_keyword_hit("祝愿祖国繁荣昌盛", "写给祖国的一句话"))
        self.assertFalse(_theme_keyword_hit("今天天气不错", "写给祖国的一句话"))
        self.assertFalse(_theme_keyword_hit("随便写点什么", ""))
        for theme, text in self.ON_THEME.items():
            self.assertTrue(_theme_keyword_hit(text, theme), f"{theme} 的词表不认自己的例子")


class WallTests(_EngineCase):
    def setUp(self):
        super().setUp()
        self.phase = "national"

    def _post(self, text):
        self.ap()["daily"]["firework"] = 0
        return self.say("贺词 " + text)

    def test_wall_shows_newest_first_with_matching_indices(self):
        for i in range(1, 6):
            self._post(f"祝福第{i}句祖国繁荣")
        body = self.say("月华墙").split("最新上墙")[1]
        self.assertIn("1. 祝福第5句祖国繁荣", body)
        self.assertIn("5. 祝福第1句祖国繁荣", body)

    def test_hot_section_ranks_by_likes(self):
        for i in range(1, 4):
            self._post(f"祝福第{i}句祖国繁荣")
        wall = self.act._data["wall"]
        wall[0]["likes"] = 9        # 最旧那条最热（编号 3）
        wall[2]["likes"] = 2        # 最新那条（编号 1）
        out = self.say("月华墙")
        hot = out.split("最受欢迎")[1].split("最新上墙")[0]
        self.assertIn("3. 祝福第1句祖国繁荣", hot)
        self.assertLess(hot.index("👍 9"), hot.index("👍 2"), "热度高者应排在前面")

    def test_empty_wall_is_handled(self):
        self.assertIn("还空着", self.say("月华墙"))


# ---------------------------------------------------------------------------
# 七、月华信息（只读，一条指令看全自己的活动状态）
# ---------------------------------------------------------------------------
class MyInfoTests(_EngineCase):
    def test_phase_sections_follow_the_current_phase(self):
        self.phase = "midautumn"
        out = self.say("月华信息")
        self.assertIn("【中秋·月耀】", out)
        self.assertNotIn("【国庆·华诞】", out)
        self.phase = "national"
        out = self.say("月华信息")
        self.assertIn("【国庆·华诞】", out)
        self.assertNotIn("【中秋·月耀】", out)
        self.assertNotIn("月饼匠心", out)
        self.phase = "both"
        out = self.say("月华信息")
        self.assertIn("【中秋·月耀】", out)
        self.assertIn("【国庆·华诞】", out)
        self.assertIn("月饼匠心", out)
        self.assertIn("双庆挑战", out)          # 只有重叠日才提示双庆

    def test_counts_and_flags_match_what_the_player_actually_did(self):
        self.phase = "both"
        self.say("拜月")
        self.say("猜灯谜")
        out = self.say("月华信息")
        self.assertIn("· 今日拜月：✅ 已完成", out)
        self.assertIn("| 猜灯谜 | 1/20 |", out)
        self.assertIn("· 今日华诞签到：⭕ 未完成", out)

    def test_rank_and_wall_posts_come_from_real_data(self):
        self.phase = "both"
        self.say("贺词 祝愿祖国繁荣昌盛")
        out = self.say("月华信息")
        self.assertIn("上墙贺词 1 条", out)
        self.assertIn("全服第 1 名", out)

    def test_is_read_only(self):
        self.phase = "both"
        self.say("做月饼 五仁")
        self.say("猜灯谜")
        before = copy.deepcopy(self.ap())
        self.say("月华信息")
        self.assertEqual(self.earned(), int(before["yuehua_earned"]))
        self.assertEqual(self.ap()["daily"], before["daily"])
        self.assertEqual(self.ap()["quiz"], before["quiz"])
        self.assertEqual(self.ap()["craft"].get("active"), before["craft"].get("active"))

    def test_aliases_return_the_same_archive(self):
        self.phase = "both"
        base = self.say("月华信息")
        self.assertEqual(self.say("我的月华"), base)
        self.assertEqual(self.say("月华档案"), base)

    def test_cold_player_never_errors_in_any_phase(self):
        for phase in ("midautumn", "national", "both"):
            self.phase = phase
            out = self.say("月华信息")
            self.assertIn("月华档案", out)
            self.assertIn("全服", out)
            self.assertIn("· 今日主题" if phase == "national" else "· 玉兔亲密度", out)

    def test_unranked_player_says_so_without_a_dangling_number(self):
        """没上榜时名次文案必须自带单位，不能拼出「全服第 未上榜 名」。"""
        self.phase = "midautumn"
        out = self.say("月华信息")
        self.assertIn("尚未上榜", out)
        self.assertNotIn("尚未上榜 名", out)


# ---------------------------------------------------------------------------
# 八、接线与铁律
# ---------------------------------------------------------------------------
class WiringTests(_EngineCase):
    NEW_COMMANDS = ("重制", "酿桂花", "取酒", "玉兔同行", "献礼", "双庆", "月华信息")
    #: 同一功能的别名，活动帮助只列主名
    ALIASES = ("我的月华", "月华档案")

    def test_new_commands_are_registered(self):
        for c in self.NEW_COMMANDS + self.ALIASES:
            self.assertIn(c, COMMANDS, f"{c} 没进 COMMANDS，会被框架过滤掉")

    def test_help_text_lists_every_command(self):
        help_text = self.say("活动帮助")
        for c in COMMANDS:
            if c in self.ALIASES:
                continue
            self.assertIn(c, help_text, f"活动帮助里没有 {c}")

    def test_phase_gates_split_the_two_festivals(self):
        self.phase = "national"
        for cmd, rest in (("酿桂花", ""), ("取酒", ""), ("玉兔同行", ""),
                          ("重制", " x"), ("做月饼", " 五仁"), ("猜灯谜", "")):
            self.assertIn("未开放", self.say(f"{cmd}{rest}"), f"{cmd} 不该在国庆段开放")
        self.phase = "midautumn"
        for cmd, rest in (("巡礼", ""), ("献礼", ""), ("贺词", " 祝祖国繁荣昌盛")):
            self.assertIn("未开放", self.say(f"{cmd}{rest}"), f"{cmd} 不该在中秋段开放")

    def test_overlap_day_opens_both_festivals(self):
        self.phase = "both"
        self.assertIn("桂花", self.say("酿桂花"))
        self.assertIn("巡礼", self.say("巡礼"))

    def test_yuehua_only_ever_goes_up(self):
        """月华只进不出：把每条指令都用最坏的方式触发一遍，累计值不许下降。"""
        self.phase = "both"
        worst = [
            "做月饼 五仁", "做月饼 五仁", "重制 完全错误", "重制 放弃", "重制 放弃",
            "酿桂花", "取酒", "取酒", "喂玉兔 桂花酿", "玉兔同行", "玉兔同行 错误答案",
            "玉兔同行 错误答案", "猜灯谜", "猜灯谜 错误答案", "猜灯谜 难题",
            "猜灯谜 难题 错误答案", "巡礼", "巡礼 错误答案", "巡礼 换线",
            "献礼", "双庆", "双庆 错误答案", "贺词 祝祖国繁荣昌盛", "点赞 1",
            "点赞 99", "月华墙", "月华榜", "里程碑", "月华信息", "活动帮助",
            "不存在的指令", "做月饼 不存在的口味",
        ]
        for text in worst:
            before = self.earned()
            self.say(text)
            self.assertGreaterEqual(self.earned(), before, f"「{text}」让月华变少了")

    def test_every_command_survives_a_cold_dispatch(self):
        """所有新指令在没有任何前置状态时都不许抛异常（线上第一条就是这个状态）。"""
        for cmd in self.NEW_COMMANDS:
            for rest in ("", "x", "1", "放弃", "换线", "难题"):
                self.say(f"{cmd} {rest}".strip())


if __name__ == "__main__":
    unittest.main()
