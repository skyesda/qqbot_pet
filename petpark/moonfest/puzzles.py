"""「月耀华诞」题库：灯谜（猜灯谜）+ 文化常识（华诞巡礼）。

题库分两层，都并进本模块的 `_LANTERNS` / `_QUIZ`（供 local_lantern / local_quiz 抽题）：

- 本文件里的手写精选题（中秋/国庆主题为主，题面质量最高，优先保留）；
- `riddles.py`（灯谜 1000+ 条）与 `quizzes.py`（巡礼 1000+ 条）两个大题库，
  它们用「结构化事实表 + 生成器」产出，答案由数据行直接给出、天然自洽。

两层按题干去重合并（手写的优先），所以最终池子只会变大、不会出现同一道题抽两次。

- 灯谜（猜灯谜，中秋段）：谜面 + 谜底，answer_type 多为 "str"，谜底为字/词/物名。
  判定优先走 Jev noul 语义等价（含谐音/别解/近义），字符串归一化比对作兜底
  （答对字面答案无条件通过）。
- 巡礼（华诞巡礼，国庆段）：国庆/中秋文化常识题，带 options/answer，answer_type
  支持 str/int；带 difficulty 1/2/3 档供 Jev 自适应难度选题（简单/中等/困难）。

判分约定（照 zhongyuan/puzzles.py:211-247）：
- answer_type == "int"：从玩家输入里取第一个整数与 answer 比较；
- answer_type == "str"：玩家输入去掉「答案是/答/第X」等前缀后与 answer 逐项匹配；
- 多字谜底额外做同义归一（见 _SYNONYM_GROUPS）：玩家答「月球」也算「月亮」，
  因为线上没配 Jev Key 时语义判定走不了，纯字符串比对会把对答案判错。
"""
from __future__ import annotations

import random
import re
from typing import Any

from . import quizzes, riddles


def _merge_bank(
    base: list[dict[str, Any]], extra: list[dict[str, Any]], key: str
) -> list[dict[str, Any]]:
    """把外部大题库并进手写题库，按题干去重——手写的条目优先，同题不会抽到两次。"""
    seen = {p.get(key) for p in base}
    out = list(base)
    for p in extra:
        if p.get(key) in seen:
            continue
        seen.add(p.get(key))
        out.append(p)
    return out

# ---------------------------------------------------------------------------
# 灯谜（中秋段「猜灯谜」）。谜底唯一、答案无歧义；题库可后台扩充。
# ---------------------------------------------------------------------------
_LANTERNS: list[dict[str, Any]] = [
    # ---- 主题：月 ----
    {"question": "十五的月亮（打一成语）", "answer": "正大光明", "answer_type": "str",
     "hint": "十五的月亮当空照，光明正大。"},
    {"question": "有时落在山腰，有时挂在树梢，有时像面圆镜，有时像把镰刀（打一物）",
     "answer": "月亮", "answer_type": "str", "hint": "每天夜里都见面，圆缺各不同。"},
    {"question": "画时圆，写时方，冬时短，夏时长（打一字）", "answer": "日", "answer_type": "str",
     "hint": "它不是月亮，是月亮的近邻，天天都升起。"},
    {"question": "日月并肩（打一字）", "answer": "明", "answer_type": "str",
     "hint": "日加月，合成一个字。"},
    {"question": "嫦娥的宫殿（打一地名）", "answer": "广寒宫", "answer_type": "str",
     "hint": "月宫别称，嫦娥与玉兔就住在这里。"},
    # ---- 主题：桂 ----
    {"question": "中秋时节满园香（打一花名）", "answer": "桂花", "answer_type": "str",
     "hint": "八月十五前后，院里院外都是它的香气。"},
    {"question": "吴刚在月宫砍的那棵树（打一植物）", "answer": "桂树", "answer_type": "str",
     "hint": "传说月中有一棵永砍不倒的树。"},
    # ---- 主题：兔 ----
    {"question": "长耳朵，红眼睛，三瓣嘴，白毛衣，月中捣药（打一动物）", "answer": "玉兔", "answer_type": "str",
     "hint": "广寒宫里陪伴嫦娥的仙兔。"},
    {"question": "耳朵长，尾巴短，红眼睛，白毛衫（打一动物）", "answer": "兔子", "answer_type": "str",
     "hint": "三瓣嘴，最爱吃胡萝卜。"},
    {"question": "龟兔赛跑里输了的动物（打一动物）", "answer": "兔子", "answer_type": "str",
     "hint": "因为半路睡觉而落败的那位。"},
    # ---- 主题：灯 ----
    {"question": "红娘子，上高楼，心里疼，眼泪流（打一物）", "answer": "蜡烛", "answer_type": "str",
     "hint": "点燃自己，照亮别人，泪滴成行。"},
    {"question": "白天草里住，晚上空中游，金光闪闪动，见尾不见头（打一物）", "answer": "萤火虫", "answer_type": "str",
     "hint": "夏夜里提着「小灯笼」飞来飞去。"},
    {"question": "一物生来身上衣，三百多件日日脱，脱到年底剩张皮（打一物）", "answer": "日历", "answer_type": "str",
     "hint": "一天撕一页，过完一年换新。"},
    {"question": "千条线，万条线，掉到水里看不见（打一自然现象）", "answer": "雨", "answer_type": "str",
     "hint": "中秋前后常见，天上下来的线。"},
    # ---- 主题：月饼 ----
    {"question": "白白身子圆溜溜，样子像个乒乓球，放在锅里煮一煮，全家吃它过中秋（打一食物）",
     "answer": "月饼", "answer_type": "str", "hint": "八月十五的节日美食，代表团圆。"},
    {"question": "扁扁圆圆，馅儿甜甜，八月十五，摆上供桌（打一食物）", "answer": "月饼", "answer_type": "str",
     "hint": "五仁、豆沙、蛋黄莲蓉……都是它的口味。"},
    # ---- 主题：中秋·成语 ----
    {"question": "中秋菊开（打一成语）", "answer": "花好月圆", "answer_type": "str",
     "hint": "菊花正盛，月亮正圆，正喻美好圆满。"},
    {"question": "嫦娥奔月（打一成语）", "answer": "一步登天", "answer_type": "str",
     "hint": "没有梯子，直接飞上了天。"},
    {"question": "中秋月最圆（打一成语）", "answer": "众望所归", "answer_type": "str",
     "hint": "大家都盼望的，正是最圆满的。"},
    {"question": "八月十五的月亮（打一成语）", "answer": "花好月圆", "answer_type": "str",
     "hint": "良辰美景，人月两团圆。"},
    # ---- 主题：中秋·字谜 ----
    {"question": "一边是红，一边是绿，一边喜风，一边喜雨（打一字）", "answer": "秋", "answer_type": "str",
     "hint": "禾喜雨，火喜风，合起来正是一个季节。"},
    {"question": "千字头，木字腰，太阳出来从下照，人人都说味道好（打一字）", "answer": "香", "answer_type": "str",
     "hint": "禾在日上，正是收获的芬芳。"},
    {"question": "一口咬掉牛尾巴（打一字）", "answer": "告", "answer_type": "str",
     "hint": "牛字尾巴被口咬去了一截。"},
    {"question": "二小二小，头上长草（打一字）", "answer": "蒜", "answer_type": "str",
     "hint": "两个「小」字叠在一起，头上顶着一棵草。"},
    {"question": "十个哥哥（打一字）", "answer": "克", "answer_type": "str",
     "hint": "十哥哥，十在上，兄在下。"},
    {"question": "山上还有山（打一字）", "answer": "出", "answer_type": "str",
     "hint": "两座山叠在一起。"},
    {"question": "半边有毛半边光，半边好吃半边香，半边山上吃青草，半边水里把身藏（打一字）",
     "answer": "鲜", "answer_type": "str", "hint": "一半水里游，一半山上跑，合起来最是美味。"},
]

_LANTERN_THEMES = ["月", "桂", "兔", "灯", "月饼", "中秋"]

# 并入 riddles.py 的大题库（1000+ 条）。手写条目排在前面，去重时优先保留。
_LANTERNS = _merge_bank(_LANTERNS, riddles.all_lanterns(), "question")
# 手写条目不写 theme（抽题时 setdefault 兜底），合并后统一补齐，池子里格式一致
for _p in _LANTERNS:
    _p.setdefault("theme", "中秋")
    _p.setdefault("source", "local")


def local_lantern() -> dict[str, Any]:
    """随机抽一道灯谜。"""
    p = random.choice(_LANTERNS)
    p.setdefault("theme", "中秋")
    p.setdefault("source", "local")
    return p


# ---------------------------------------------------------------------------
# 巡礼文化常识（国庆段「华诞巡礼」）。带 options/answer/difficulty。
# difficulty：1=简单 2=中等 3=困难（供 Jev 自适应难度选题，兜底取全部）。
# ---------------------------------------------------------------------------
_QUIZ: list[dict[str, Any]] = [
    # ---- 难度 1（简单）----
    {"q": "国庆节是每年的几月几日？", "options": ["9月30日", "10月1日", "10月2日", "10月7日"],
     "answer": "10月1日", "answer_type": "str", "difficulty": 1, "theme": "national"},
    {"q": "五星红旗上有几颗星星？", "options": ["1", "3", "5", "7"],
     "answer": "5", "answer_type": "str", "difficulty": 1, "theme": "national"},
    {"q": "我国的国歌叫什么名字？", "options": ["歌唱祖国", "东方红", "义勇军进行曲", "我的祖国"],
     "answer": "义勇军进行曲", "answer_type": "str", "difficulty": 1, "theme": "national"},
    {"q": "中国的首都是哪座城市？", "options": ["上海", "广州", "北京", "深圳"],
     "answer": "北京", "answer_type": "str", "difficulty": 1, "theme": "national"},
    {"q": "中秋节是农历的哪一天？", "options": ["七月初七", "八月十五", "九月初九", "五月初五"],
     "answer": "八月十五", "answer_type": "str", "difficulty": 1, "theme": "midautumn"},
    {"q": "中秋节的传统美食是什么？", "options": ["粽子", "汤圆", "月饼", "饺子"],
     "answer": "月饼", "answer_type": "str", "difficulty": 1, "theme": "midautumn"},
    {"q": "我国一共有多少个民族？", "options": ["54", "55", "56", "57"],
     "answer": "56", "answer_type": "str", "difficulty": 1, "theme": "national"},
    {"q": "天安门广场每天清晨升起的旗帜叫什么？", "options": ["红旗", "五星红旗", "大红旗", "国旗"],
     "answer": "五星红旗", "answer_type": "str", "difficulty": 1, "theme": "national"},
    {"q": "「举头望明月」的下一句是什么？", "options": ["低头思故乡", "疑是地上霜", "月是故乡明", "千里共婵娟"],
     "answer": "低头思故乡", "answer_type": "str", "difficulty": 1, "theme": "midautumn"},
    {"q": "中秋节又被称为什么节？", "options": ["团圆节", "敬老节", "踏青节", "龙舟节"],
     "answer": "团圆节", "answer_type": "str", "difficulty": 1, "theme": "midautumn"},
    # ---- 难度 2（中等）----
    {"q": "中华人民共和国成立于哪一年？", "options": ["1947", "1948", "1949", "1950"],
     "answer": "1949", "answer_type": "str", "difficulty": 2, "theme": "national"},
    {"q": "我国陆地面积在世界上排第几？", "options": ["第一", "第二", "第三", "第四"],
     "answer": "第三", "answer_type": "str", "difficulty": 2, "theme": "national"},
    {"q": "我国面积最大的省级行政区是哪个？", "options": ["西藏自治区", "内蒙古自治区", "新疆维吾尔自治区", "青海省"],
     "answer": "新疆维吾尔自治区", "answer_type": "str", "difficulty": 2, "theme": "national"},
    {"q": "我国国徽图案中的主体建筑是什么？", "options": ["华表", "天安门", "长城", "人民英雄纪念碑"],
     "answer": "天安门", "answer_type": "str", "difficulty": 2, "theme": "national"},
    {"q": "「但愿人长久」的下一句是什么？", "options": ["千里共婵娟", "月是故乡明", "低头思故乡", "天涯共此时"],
     "answer": "千里共婵娟", "answer_type": "str", "difficulty": 2, "theme": "midautumn"},
    {"q": "五星红旗上那颗大的五角星代表什么？", "options": ["工农联盟", "中国共产党", "全国人民", "人民解放军"],
     "answer": "中国共产党", "answer_type": "str", "difficulty": 2, "theme": "national"},
    {"q": "我国最长的河流是哪条？", "options": ["黄河", "珠江", "长江", "黑龙江"],
     "answer": "长江", "answer_type": "str", "difficulty": 2, "theme": "national"},
    {"q": "世界上最高的山峰是哪座？", "options": ["乔戈里峰", "贡嘎山", "珠穆朗玛峰", "梅里雪山"],
     "answer": "珠穆朗玛峰", "answer_type": "str", "difficulty": 2, "theme": "national"},
    {"q": "中秋节的起源与哪个神话传说有关？", "options": ["女娲补天", "嫦娥奔月", "精卫填海", "夸父逐日"],
     "answer": "嫦娥奔月", "answer_type": "str", "difficulty": 2, "theme": "midautumn"},
    {"q": "我国的国宝动物是什么？", "options": ["金丝猴", "华南虎", "大熊猫", "藏羚羊"],
     "answer": "大熊猫", "answer_type": "str", "difficulty": 2, "theme": "national"},
    # ---- 难度 3（困难）----
    {"q": "我国国徽中，齿轮和麦稻穗分别象征什么？", "options": ["工业与农业", "城市与乡村", "工人与农民", "南方与北方"],
     "answer": "工人与农民", "answer_type": "str", "difficulty": 3, "theme": "national"},
    {"q": "《义勇军进行曲》的曲作者是谁？", "options": ["冼星海", "聂耳", "田汉", "贺绿汀"],
     "answer": "聂耳", "answer_type": "str", "difficulty": 3, "theme": "national"},
    {"q": "我国恢复联合国安理会常任理事国合法席位是在哪一年？", "options": ["1969", "1971", "1975", "1978"],
     "answer": "1971", "answer_type": "str", "difficulty": 3, "theme": "national"},
    {"q": "我国第一颗人造地球卫星叫什么名字？", "options": ["神舟一号", "东方红一号", "长征一号", "风云一号"],
     "answer": "东方红一号", "answer_type": "str", "difficulty": 3, "theme": "national"},
    {"q": "我国首次实现月球背面软着陆的探测器是哪个？", "options": ["嫦娥三号", "嫦娥四号", "嫦娥五号", "玉兔二号"],
     "answer": "嫦娥四号", "answer_type": "str", "difficulty": 3, "theme": "midautumn"},
    {"q": "「中秋」这一名称的由来与哪句话有关？", "options": ["三秋之半", "秋分之半", "月半之时", "仲夏之末"],
     "answer": "三秋之半", "answer_type": "str", "difficulty": 3, "theme": "midautumn"},
]

_QUIZ_OPTION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# 并入 quizzes.py 的大题库（1000+ 条）。手写条目排在前面，去重时优先保留。
_QUIZ = _merge_bank(_QUIZ, quizzes.all_quizzes(), "q")


def local_quiz(difficulty: int = 2) -> dict[str, Any]:
    """抽一道巡礼题。difficulty 指定档位（1/2/3）；该档无题时退回到全部题库。"""
    pool = [p for p in _QUIZ if int(p.get("difficulty", 2)) == difficulty] or list(_QUIZ)
    p = random.choice(pool)
    p.setdefault("source", "local")
    return p


def quiz_options_text(p: dict) -> str:
    """把巡礼题的 options 渲染成「A. xxx B. xxx …」文本。"""
    parts = []
    for i, opt in enumerate(p.get("options") or []):
        parts.append(f"{_QUIZ_OPTION_LETTERS[i]}. {opt}")
    return " ".join(parts)


def option_index_to_text(p: dict, letter: str) -> str | None:
    """选项序号字母（A/B/C…）→ 选项原文；越界返回 None。"""
    letter = (letter or "").strip().upper()
    idx = _QUIZ_OPTION_LETTERS.find(letter)
    opts = p.get("options") or []
    if idx < 0 or idx >= len(opts):
        return None
    return opts[idx]


# ---------------------------------------------------------------------------
# 答案归一化与判定（照 zhongyuan/puzzles.py:211-247）
# ---------------------------------------------------------------------------
_INT_RE = re.compile(r"\d+")

# 谜底同义答法。灯谜/物谜的谜底是日常词，同一样东西常有多种叫法：题库里「月亮」和
# 「月球」、「兔子」和「玉兔」本身就是各自的谜底，玩家换一种写法答对同一个东西，纯
# 字符串比对会判错。线上当前**没配 Jev Key**（语义判定走不了），这层本地容错就是唯一
# 兜底，所以必须补上。
#
# 只对「多字谜底 + 无选项的开放题」生效：
# - 字谜谜底是单个汉字（日/月/秋…），把「日」等同于「太阳」会让「打一字」的题被
#   「太阳」蒙对；
# - 巡礼选择题要求与某个选项逐字相等，而答案与干扰项常是「中秋节 / 中秋」这类近义对，
#   归一化会把干扰项也判对。
_SYNONYM_GROUPS: list[tuple[str, ...]] = [
    ("月亮", "月球", "明月"),
    ("兔子", "玉兔", "小白兔", "小兔子", "兔儿"),
    ("中秋节", "中秋", "团圆节", "八月节"),
    ("国庆节", "国庆", "十月一日"),
    ("秋天", "秋季", "秋"),
    ("五星红旗", "国旗", "红旗"),
    ("义勇军进行曲", "国歌"),
    ("桂花", "桂花树", "桂树", "桂"),
    ("灯笼", "花灯", "灯"),
    ("心愿灯", "孔明灯", "天灯", "许愿灯"),
    ("月饼", "中秋月饼"),
    ("天空", "天上"),
    ("太阳", "日头"),
]
_SYNONYM: dict[str, str] = {
    word: group[0] for group in _SYNONYM_GROUPS for word in group
}


def _canon_answer(text: str, puzzle: dict) -> str:
    """把多字开放题的答案归一到一个代表写法；不符合条件时原样返回。"""
    if puzzle.get("options") or len(str(puzzle.get("answer") or "")) < 2:
        return text
    return _SYNONYM.get(text, text)


def normalize_answer(user_text: str, puzzle: dict) -> str | None:
    """把玩家输入归一化为可判分的形式；无法判定返回 None。"""
    t = (user_text or "").strip()
    if not t:
        return None
    if puzzle.get("answer_type") == "int":
        m = _INT_RE.search(t)
        return m.group(0) if m else None
    # str：剥离常见前缀后与选项/答案比对
    t2 = re.sub(r"^(答案是|答案|答|选|我选|第|是)", "", t).strip()
    options = puzzle.get("options") or []
    for opt in options:
        if opt and (t == opt or t2 == opt):
            return opt
    # 用户输入纯数字 → 视为选项序号（1 起），对齐到对应选项原文
    if t2.isdigit() and options:
        idx = int(t2) - 1
        if 0 <= idx < len(options):
            return options[idx]
    # 巡礼题：字母选项（A/B/C…）→ 选项原文
    letter = option_index_to_text(puzzle, t2)
    if letter is not None:
        return letter
    # 兜底：答案关键词包含在输入里
    ans = str(puzzle.get("answer", ""))
    if ans and ans in t:
        return ans
    return _canon_answer(t2 or t, puzzle)


def is_correct(user_text: str, puzzle: dict) -> bool:
    """判断玩家答案是否正确（多字开放题按同义写法归一后比较）。"""
    ans = str(puzzle.get("answer", ""))
    if not ans:
        return False
    norm = normalize_answer(user_text, puzzle)
    if norm is None:
        return False
    return norm == _canon_answer(ans, puzzle)
