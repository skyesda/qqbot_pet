"""Build a frozen 1,000-question National Day bank; never runs on a player request."""
import ast
import itertools
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RNG = random.Random(20261001)
QUESTIONS = []

# Stable geography facts are reused as data, not old quiz wording or old quiz IDs.
tree = ast.parse((ROOT / 'petpark/moonfest/quizzes.py').read_text(encoding='utf-8'))
facts = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        key = node.targets[0].id
        if key in {'_PROVINCES', '_CITY_PROVINCE', '_RELICS'}:
            facts[key] = ast.literal_eval(node.value)
provinces = facts['_PROVINCES']
prov_by_name = {p[0]: p for p in provinces}


def add(q, answer, wrong, category, explanation):
    options = list(dict.fromkeys([str(answer)] + [str(x) for x in wrong if str(x) != str(answer)]))
    if len(options) < 4:
        raise ValueError(q)
    options = options[:4]
    RNG.shuffle(options)
    QUESTIONS.append(dict(id=f'N{len(QUESTIONS)+1:04}', question=q, options=options,
                          answer=options.index(str(answer)), category=category, explanation=explanation))


def others(pool, answer):
    return RNG.sample(sorted(set(pool) - {answer}), 3)


for name, capital, abbreviation, region in provinces:
    explanation = f'{name}的省级行政中心为{capital}，简称可用{abbreviation}，地理分区为{region}。'
    category = '山河巡礼·省级行政区'
    add(f'国庆省区巡礼：{name}的省级行政中心是哪座城市？', capital, others([p[1] for p in provinces], capital), category, explanation)
    add(f'国庆地图拼图：标有“{abbreviation}”的省区应拼到哪个省级行政区？', name, others([p[0] for p in provinces], name), category, explanation)
    add(f'山河知识卡：{name}属于我国哪个地理分区？', region, others([p[3] for p in provinces], region), category, explanation)
    add(f'国庆邮戳配对：省级行政中心为{capital}的省区，下面哪个简称与它对应？', abbreviation, others([p[2] for p in provinces], abbreviation), category, explanation)
    correct = f'{name}—{capital}—{abbreviation}'
    add(f'国庆省区展板：哪一组关于{name}的“省区—行政中心—简称”对应全部正确？', correct,
        [f'{name}—{c}—{abbreviation}' for c in others([p[1] for p in provinces], capital)], category, explanation)
    add(f'国庆跨省路线：先到{capital}这座省级行政中心，它所在的省区属于哪个地理分区？', region,
        others([p[3] for p in provinces], region), category, explanation)

for city, province in facts['_CITY_PROVINCE']:
    abbreviation = prov_by_name[province][2]
    add(f'国庆城市明信片：{city}所在的省级行政区是哪一个？', province,
        others([p[0] for p in provinces], province), '山河巡礼·城市', f'{city}位于{province}。')
    add(f'国庆城市与省区联考：去{city}旅行时，目的地省区的哪个简称是正确的？', abbreviation,
        others([p[2] for p in provinces], abbreviation), '山河巡礼·城市', f'{city}位于{province}，其省区简称可用{abbreviation}。')

for landmark, province in facts['_RELICS']:
    add(f'国庆文化之旅：参观{landmark}，应前往哪个省级行政区？', province,
        others([p[0] for p in provinces], province), '山河巡礼·文化名胜', f'{landmark}位于{province}。')
    correct = f'{landmark}—{province}'
    add(f'国庆旅游展板审核：关于{landmark}的哪组“名胜—省区”搭配正确？', correct,
        [f'{landmark}—{p}' for p in others([p[0] for p in provinces], province)], '山河巡礼·文化名胜', f'{landmark}位于{province}。')

CORE = [
 ('国庆节日期','10月1日',['9月30日','10月2日','10月7日']),
 ('国庆日纪念的历史事件','中华人民共和国成立',['抗日战争胜利','中国人民解放军建军','五四运动']),
 ('开国大典举行的城市','北京',['南京','上海','西安']),
 ('开国大典举行的年份','1949年',['1945年','1950年','1954年']),
 ('国庆日决议通过的日期','1949年12月2日',['1949年10月1日','1950年10月1日','1949年9月21日']),
 ('国庆日决议的通过机关','中央人民政府委员会',['国务院','全国人民代表大会','中国人民政治协商会议第一届全体会议']),
 ('中华人民共和国国旗的名称','五星红旗',['五环旗','八一军旗','红十字旗']),
 ('国旗旗面的底色','红色',['蓝色','绿色','白色']),
 ('国旗五角星的颜色','黄色',['白色','蓝色','绿色']),
 ('国旗上的五角星总数','5颗',['1颗','4颗','6颗']),
 ('国旗上的大五角星数量','1颗',['2颗','4颗','5颗']),
 ('国旗上的小五角星数量','4颗',['1颗','3颗','5颗']),
 ('中华人民共和国国歌名称','《义勇军进行曲》',['《歌唱祖国》','《东方红》','《我和我的祖国》']),
 ('国歌的词作者','田汉',['聂耳','冼星海','贺绿汀']),
 ('国歌的曲作者','聂耳',['田汉','冼星海','光未然']),
 ('国徽中央的建筑','天安门',['故宫太和殿','人民大会堂','人民英雄纪念碑']),
 ('升旗过程中在场人员的适当行为','面向国旗肃立并按规定行礼',['背对国旗交谈','随意奔跑','大声嬉闹']),
 ('损坏或褪色国旗的适当处理','按规定更换和处置',['继续随意悬挂','当作普通抹布','丢在公共道路上']),
 ('中华人民共和国首都','北京',['上海','南京','广州']),
 ('2026年国庆时新中国成立的周年数','77周年',['76周年','78周年','80周年']),
]
for label, answer, wrong in CORE:
    explanation = f'{label}：{answer}。'
    add(f'国庆基础知识：{label}是什么？', answer, wrong, '国庆沿革与国家象征', explanation)
    add(f'国庆知识展板核对：“{label}”一栏应填写哪项？', answer, wrong, '国庆沿革与国家象征', explanation)
    statement = f'{label}是{answer}'
    add(f'国庆知识纠错：下面关于“{label}”的哪项说法正确？', statement,
        [f'{label}是{w}' for w in wrong], '国庆沿革与国家象征', explanation)

TIMELINE = [
 (1949,'中华人民共和国成立'), (1953,'第一个五年计划开始实施'),
 (1954,'第一部中华人民共和国宪法通过'), (1956,'第一辆解放牌汽车下线'),
 (1958,'中国第一台通用电子数字计算机研制成功'), (1959,'人民大会堂建成'),
 (1964,'中国第一颗原子弹爆炸成功'), (1967,'中国第一颗氢弹爆炸成功'),
 (1970,'东方红一号卫星成功发射'), (1971,'中华人民共和国恢复联合国合法席位'),
 (1978,'中共十一届三中全会召开'), (1980,'深圳等经济特区设立'),
 (1997,'香港回归祖国'), (1999,'澳门回归祖国'), (2001,'中国加入世界贸易组织'),
 (2003,'神舟五号完成首次载人航天飞行'), (2007,'嫦娥一号成功发射'),
 (2008,'北京举办夏季奥运会'), (2011,'天宫一号成功发射'),
 (2013,'嫦娥三号实现月面软着陆'), (2016,'FAST射电望远镜落成启用'),
 (2017,'C919大型客机完成首飞'), (2018,'港珠澳大桥正式开通'),
 (2019,'嫦娥四号实现人类首次月球背面软着陆'), (2020,'嫦娥五号完成月球采样返回'),
 (2021,'中国空间站天和核心舱成功发射'), (2022,'中国空间站形成T字基本构型'),
 (2023,'C919大型客机完成首次商业载客飞行'), (2024,'嫦娥六号完成月球背面采样返回'),
]
for year, event in TIMELINE:
    add(f'国庆成就回顾：“{event}”发生在哪一年？', f'{year}年',
        [f'{x}年' for x in others([p[0] for p in TIMELINE], year)], '新中国建设历程', f'{event}发生于{year}年。')
    add(f'共和国时间轴：{year}年发生的下列哪件事与年份对应正确？', event,
        others([p[1] for p in TIMELINE], event), '新中国建设历程', f'{year}年：{event}。')

# Chronological reasoning asks a different task from factual recall. Both event
# dates are in the frozen explanation; no invented dates or random arithmetic.
pairs = list(itertools.combinations(TIMELINE, 2))
RNG.shuffle(pairs)
for (y1, e1), (y2, e2) in pairs:
    if len(QUESTIONS) == 1000:
        break
    if RNG.random() < .5:
        y1, e1, y2, e2 = y2, e2, y1, e1
    early, late = (e1, e2) if y1 < y2 else (e2, e1)
    add(f'国庆成就时间轴：“{e1}”与“{e2}”的先后关系是哪一种？', f'{early}先发生',
        [f'{late}先发生','两件事发生于同一年','两件事都发生在1949年以前'], '新中国历程·时序辨析',
        f'{e1}：{y1}年；{e2}：{y2}年。{early}发生更早。')

assert len(QUESTIONS) == 1000
assert len({q['question'] for q in QUESTIONS}) == 1000
assert all(len(set(q['options'])) == 4 for q in QUESTIONS)
output = ROOT / 'petpark/moonfest/national_questions.json'
output.write_text(json.dumps(QUESTIONS, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(output, len(QUESTIONS))
