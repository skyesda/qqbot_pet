"""月耀华诞题库契约：题量、题干唯一性、字段完整性与判分容错。

题库由「手写精选题 + riddles.py/quizzes.py 大题库」合并而成（见 moonfest/puzzles.py），
这里把合并后的硬约束固化下来：题量掉回 1000 以下、出现重复题干、或同义答法判分被改坏，
都能在这里立刻发现。
"""
import sys
import unittest
from pathlib import Path

# moonfest 包的 __init__ 会导入 engine，engine 依赖框架的 astrbot；本地跑测试时用
# petbot_framework/compat 下的桩补上（test_adventure.py 也是这么做的）。
_COMPAT = Path(__file__).resolve().parents[2] / "petbot_framework" / "compat"
if _COMPAT.is_dir() and str(_COMPAT) not in sys.path:
    sys.path.insert(0, str(_COMPAT))

from qqbot_pet.petpark.moonfest import puzzles


class LanternBankTests(unittest.TestCase):
    def test_bank_is_large_and_unique(self):
        self.assertGreaterEqual(len(puzzles._LANTERNS), 1000)
        questions = [p["question"] for p in puzzles._LANTERNS]
        self.assertEqual(len(questions), len(set(questions)), "灯谜谜面有重复")

    def test_every_item_is_well_formed(self):
        for p in puzzles._LANTERNS:
            for key in ("question", "answer", "answer_type", "hint", "theme"):
                self.assertTrue(p.get(key), f"灯谜缺字段 {key}: {p!r}")
            self.assertEqual(p["answer_type"], "str")

    def test_hint_never_leaks_the_answer(self):
        leaked = [p for p in puzzles._LANTERNS if p["answer"] in p["hint"]]
        self.assertEqual(leaked, [], f"提示里写出了谜底，等于白给：{leaked[:3]!r}")


class QuizBankTests(unittest.TestCase):
    def test_bank_is_large_unique_and_has_enough_per_difficulty(self):
        self.assertGreaterEqual(len(puzzles._QUIZ), 1000)
        stems = [p["q"] for p in puzzles._QUIZ]
        self.assertEqual(len(stems), len(set(stems)), "巡礼题干有重复")
        for difficulty in (1, 2, 3):
            pool = [p for p in puzzles._QUIZ if int(p.get("difficulty", 2)) == difficulty]
            # 每档都要够抽：local_quiz 按档取题，某档太少会频繁抽到同一道
            self.assertGreaterEqual(len(pool), 250, f"难度 {difficulty} 的题不够 250 道")

    def test_every_item_is_well_formed(self):
        for p in puzzles._QUIZ:
            options = p.get("options") or []
            self.assertEqual(len(options), 4, f"选项不是 4 个：{p['q']}")
            self.assertEqual(len(set(options)), 4, f"选项有重复：{p['q']}")
            self.assertIn(p["answer"], options, f"答案不在选项里：{p['q']}")
            self.assertEqual(p["answer_type"], "str")
            self.assertIn(int(p["difficulty"]), (1, 2, 3))
            self.assertIn(p["theme"], ("national", "midautumn"))


class AnswerToleranceTests(unittest.TestCase):
    """判分容错：多字谜底认同义写法，字谜（单字）与选择题仍须精确命中。"""

    def correct(self, text, puzzle):
        return puzzles.is_correct(text, puzzle)

    def test_multi_char_answer_accepts_synonyms(self):
        for text, answer in (("月球", "月亮"), ("小白兔", "兔子"), ("桂花", "桂树"),
                             ("中秋", "中秋节"), ("孔明灯", "心愿灯")):
            self.assertTrue(self.correct(text, {"answer": answer, "answer_type": "str"}),
                            f"「{text}」应该算「{answer}」答对")

    def test_single_char_answer_is_not_widened(self):
        # 字谜谜底是单个汉字，把「日」等同于「太阳」会让「打一字」被蒙对
        self.assertFalse(self.correct("月亮", {"answer": "日", "answer_type": "str"}))

    def test_multiple_choice_stays_exact(self):
        # 选择题答案与干扰项常是「中秋节 / 中秋」这类近义对，归一化会把干扰项判对
        puzzle = {"answer": "中秋节", "answer_type": "str",
                  "options": ["中秋节", "中秋", "重阳节", "七夕节"]}
        self.assertTrue(self.correct("中秋节", puzzle))
        self.assertFalse(self.correct("中秋", puzzle))
        self.assertTrue(self.correct("A", puzzle))   # 字母选项
        self.assertTrue(self.correct("1", puzzle))   # 序号选项


class DrawTests(unittest.TestCase):
    def test_draw_functions_return_usable_items(self):
        lantern = puzzles.local_lantern()
        self.assertTrue(lantern.get("question") and lantern.get("answer"))
        for difficulty in (1, 2, 3):
            quiz = puzzles.local_quiz(difficulty)
            self.assertEqual(int(quiz["difficulty"]), difficulty)
            self.assertTrue(puzzles.quiz_options_text(quiz))


if __name__ == "__main__":
    unittest.main()
