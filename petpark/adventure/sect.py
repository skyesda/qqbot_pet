"""群级多宗门：任务楼 / 北秘境 / 南金库 / 西仓库 / 星辰阁 + 帮贡经济 + 活跃度扩容。

修士为主、灵宠为次。一个群可立多个宗门；每人只能加入一个宗门。建宗消耗 2000 天晶。
宗门人数上限=10 起步，每累计 40 活跃度扩招 1 人，封顶 20（活跃度来自成员做任务/探索/镇守）。
成员积累个人帮贡，宗门共享库存（treasury）用于升级与星辰阁合成。战斗复用 combat.simulate。
所有守卫经 service.require / RuleError，由 service.handle 深拷贝回滚兜底。
"""
from __future__ import annotations

import random
from . import content as c

SEC_MAX_LEVEL = 10

# 人数上限：基础 10，每 ACTIVITY_PER_CAP 活跃度 +1，封顶 SECT_CAP_MAX。
SECT_BASE_CAP = 10
SECT_CAP_MAX = 20
ACTIVITY_PER_CAP = 40

# 活跃度加成：任务/探索/镇守（胜负不同）。
_ACTIVITY = {"mission": 2, "explore": 3, "guard_win": 5, "guard_loss": 1}

# 建筑：只有"交互类"单独做指令；被动类在数值结算点乘一个宗门系数。
BUILDINGS = {
    "mission":   {"name": "任务楼", "unlock": 1, "kind": "interactive"},
    "warehouse": {"name": "西仓库", "unlock": 1, "kind": "interactive"},
    "north":     {"name": "北秘境", "unlock": 2, "kind": "interactive"},
    "gold":      {"name": "南金库", "unlock": 3, "kind": "interactive"},
    "star":      {"name": "星辰阁", "unlock": 4, "kind": "interactive"},
    "martial":   {"name": "练武堂", "unlock": 4, "kind": "passive", "buff": "cult"},
    "smith":     {"name": "铁匠铺", "unlock": 5, "kind": "passive", "buff": "forge"},
    "research":  {"name": "研究院", "unlock": 6, "kind": "passive", "buff": "insight"},
}

# 每日次数上限（已计入数值平衡，勿随意调大以防刷爆主线）。
DAILY_LIMITS = {"mission": 3, "guard": 1, "explore": 2}

# 宗门操作冷却（秒）：与宠物侧「副本/修行」的冷却节奏对齐，防连点刷取，但不叠在主线限次之外。
_SECT_CD = {"mission": 60, "explore": 300, "guard": 600, "star": 300}

# 宗门任务目标池：从「今日真实活动」里挑一个需要实际完成的任务，而不是一键白拿。
# read 读当前进度（随今日各活动计数变化），cap 为当日上限；接取时按剩余余量裁剪，保证任务必可完成。
_MISSION_SOURCES = {
    "rewards":    ("打怪（历练/副本）", lambda a, s, qq: int(a.get("rewards", 0)), 8),
    "world_hits": ("讨伐世界首领", lambda a, s, qq: int(a.get("world_hits", 0)), 3),
    "trial":      ("通关地图（试炼秘境）", lambda a, s, qq: int((a.get("trial") or {}).get("used", 0)), 2),
    "guard":      ("镇守宗门", lambda a, s, qq: int(_count(s, qq, "guard")), 1),
    "explore":    ("探秘北秘境", lambda a, s, qq: int(_count(s, qq, "explore")), 2),
}
# 宗门每日任务：每次接取随机 二选一（打怪两次 / 通关地图一次），接取后须真正去打才能达成。
_MISSION_CHOICES = [("rewards", 2), ("trial", 1)]


def _today(service):
    return service.today()


def _role_of(s, qq):
    """返回该成员角色名（未加入返回空串）。"""
    m = (s.get("members") or {}).get(qq)
    return (m or {}).get("role", "") if isinstance(m, dict) else ""


def _is_officer(role):
    return role in ("帮主", "长老")


def _reset_daily(s, day):
    """惰性重置某开放日期的每日计数（只保留当前日）。"""
    if s.get("daily_date") != day:
        s["daily_date"] = day
        s["daily"] = {}


def _count(s, qq, key):
    return int((s.get("daily") or {}).get(qq, {}).get(key, 0))


def _bump(s, qq, key):
    s.setdefault("daily", {}).setdefault(qq, {})
    s["daily"][qq][key] = _count(s, qq, key) + 1


def _cd_until(service, a, kind, seconds):
    """登记某类宗门操作的冷却（同一类共享一个时钟，读 service.clock()）。"""
    a.setdefault("sect_cds", {})[kind] = int(service.clock()) + seconds


def _cd_wait(service, a, kind):
    """返回某类宗门操作剩余冷却秒数，0 表示可执行。"""
    return max(0, int(a.setdefault("sect_cds", {}).get(kind, 0)) - int(service.clock()))


def _treasury_split(s, qq, contribution, split=0.5):
    """个人帮贡入库：分润到 treasury（默认一半），并计入个人与宗门累计贡献。"""
    s["treasury"] = s.get("treasury", 0) + int(contribution * split)
    member = s["members"].setdefault(qq, {"role": "帮众", "contribution": 0, "joined_at": s.get("created_at", 0)})
    member["contribution"] = int(member.get("contribution", 0)) + contribution
    s["total_contribution"] = int(s.get("total_contribution", 0)) + contribution


def _pay_treasury(s, amount):
    """扣宗门库存，不够返回 False。"""
    if s.get("treasury", 0) < amount:
        return False
    s["treasury"] -= amount
    return True


def sect_cap(s) -> int:
    """当前人数上限：基础 10，每 ACTIVITY_PER_CAP 活跃度 +1，封顶。"""
    return min(SECT_CAP_MAX, SECT_BASE_CAP + int(s.get("activity", 0)) // ACTIVITY_PER_CAP)


def _new_sect_id(store) -> str:
    sects = store._data.setdefault("sects", {})
    n = 1
    while f"sect{n}" in sects:
        n += 1
    return f"sect{n}"


def _guard_encounter(service, s):
    """南金库镇守战：敌人数值随宗门等级上涨。"""
    lv = s.get("level", 1)
    scale = 0.9 + lv * 0.18
    return {"boss": f"{BUILDINGS['gold']['name']}妖兵", "scale": scale, "mechanic": "strike"}


def _mission_reward(service, s, a):
    """宗门任务（任务楼）：修为 + 灵材 + 帮贡，随宗门/建筑等级上涨。"""
    sect_lv = s.get("level", 1)
    mission_lv = int((s.get("buildings") or {}).get("mission", 1))
    cult = int((40 + 20 * sect_lv) * (1 + 0.05 * mission_lv))
    ore = 1 + sect_lv
    contrib = 20 + 10 * sect_lv
    return cult, ore, contrib


def _explore_reward(service, s, a):
    """北秘境：修为 + 灵材 + 帮贡，附概率掉落经验书/灵宠经验。"""
    sect_lv = s.get("level", 1)
    xlv = a.get("level", 1)
    cult = 50 + xlv * 3
    ore = 2 + xlv // 20
    contrib = 10 + 5 * sect_lv
    returned = {"cult": cult, "ore": ore, "contrib": contrib, "bonus": ""}
    if random.random() < 0.2:
        item = random.choice(["小经验书", "中经验书"])
        returned["bonus"] = f"、额外掉落『{item}』"
        returned["item"] = item
    return returned


def handle(service, group, key, cmd, args, p, a):
    """宗门指令分发入口（由 service._handle 在 cmd in SECT_COMMANDS 时调用）。"""
    qq = str(p["qq"])

    if cmd == "创建宗门":
        return _create(service, group, key, p, a, qq, args)
    if cmd == "申请入宗":
        return _apply(service, group, key, p, qq, args)
    if cmd == "宗门榜":
        return _roster_all(service, group)

    # 其余指令都针对"我所在宗门"。
    sid, s = service.store.my_sect(group, qq)
    if not s or not s.get("name"):
        return "本群还没有你加入的宗门。可「创建宗门 名称」或「申请入宗 宗名」。"
    _reset_daily(s, _today(service))

    if cmd == "同意入宗":
        return _approve(service, group, key, p, qq, s, sid, args)
    if cmd == "拒绝入宗":
        return _reject(service, key, p, qq, s, args)
    if cmd == "退出宗门":
        return _quit(service, group, key, p, qq, s, sid)
    if cmd == "查看宗门":
        return _view(service, s)
    if cmd == "宗门名册":
        return _roster(service, s)
    if cmd == "宗门公告":
        return _announce(service, key, p, qq, s, args)
    if cmd == "宗门升级":
        return _upgrade(service, key, p, qq, s)
    if cmd == "宗门捐献":
        return _donate(service, key, p, a, qq, s, args)
    if cmd == "封官":
        return _promote(service, key, p, qq, s, args)
    if cmd == "免职":
        return _demote(service, key, p, qq, s, args)
    if cmd == "踢出宗门":
        return _kick(service, group, key, p, qq, s, sid, args)
    if cmd == "宗门任务":
        return _do_mission(service, key, p, a, s, qq)
    if cmd == "宗门探索":
        return _do_explore(service, key, p, a, s, qq)
    if cmd == "镇守宗门":
        return _do_guard(service, key, p, a, s, qq)
    if cmd == "宗门兑换":
        return _do_exchange(service, key, p, a, s, qq, args)
    if cmd == "星辰阁":
        return _do_star(service, key, p, a, s, qq, args)
    return "未知宗门指令。"


def _pid(service, raw):
    """把用户输入解析为存档内存储的平台用户ID：是已绑定QQ号则反查 openid，否则原样（无绑定回退用户ID）。"""
    raw = str(raw).strip()
    if not raw:
        return raw
    return service.store.find_platform_id_by_qq(raw) or raw


def _name(service, uid):
    """显示成员时优先用已绑定QQ号，未绑定回退显示用户ID。"""
    uid = str(uid)
    return service.store.get_bound_qq(uid) or uid


def _create(service, group, key, p, a, qq, args):
    service.can_edit(key)
    name = args[0] if args else ""
    service.require(name, "创建宗门 名称（1-8字）。")
    service.require(len(name) <= 8, "宗门名最多8个字。")
    service.require(not service.store.my_sect(group, qq)[0], "你已在本群加入宗门，一人只能加入一个宗门。")
    service.require(not service.store.sect_by_name(group, name)[0], f"本群已有宗门「{name}」，请换一个名字。")
    service.require(service.store.get_currency(p, "天晶") >= 2000, "创建宗门需要 2000 天晶（天晶由生辰盛典/首领奖励等获得）。")
    sid = _new_sect_id(service.store)
    service.store.add_currency(p, "天晶", -2000)
    s = service.store._default_sect_state(group=service.store.resolve_group(str(group)), name=name)
    s["level"] = 1
    s["treasury"] = 0
    s.update(founder=qq, created_at=int(service.clock()),
             members={qq: {"role": "帮主", "contribution": 0, "joined_at": int(service.clock())}},
             pending={})
    service.store._data.setdefault("sects", {})[sid] = s
    service.store.bind_sect(group, qq, sid)
    return f"宗门「{name}」已创立！发送「申请入宗 宗名」招同修，或「宗门任务」开始赚帮贡。"


def _apply(service, group, key, p, qq, args):
    service.can_edit(key)
    name = args[0] if args else ""
    service.require(name, "申请入宗 宗名（加哪个宗门）。发送「宗门榜」查看本群宗门。")
    sid, s = service.store.sect_by_name(group, name)
    service.require(sid, f"本群没有「{name}」这个宗门。可「宗门榜」查看现有宗门。")
    service.require(not service.store.my_sect(group, qq)[0], "你已在本群加入宗门，一人只能加入一个宗门。")
    service.require(qq not in (s.get("pending") or {}), "申请已提交，请等待审批。")
    if len(s.get("members") or {}) >= sect_cap(s):
        service.require(False, f"「{s['name']}」人数已满（{sect_cap(s)}人），可让宗门做任务/探索提升活跃度扩招，或另寻宗门。")
    s.setdefault("pending", {})[qq] = int(service.clock())
    return f"已提交入宗申请，等待「{s['name']}」帮主/长老审批。"


def _approve(service, group, key, p, qq, s, sid, args):
    service.can_edit(key)
    service.require(_is_officer(_role_of(s, qq)), "只有帮主/长老可审批申请。")
    target = _pid(service, args[0] if args else "")
    service.require(target in (s.get("pending") or {}), "没有该成员的待审批申请。")
    service.require(not service.store.my_sect(group, target)[0], f"{_name(service, target)} 已加入其他宗门。")
    if len(s.get("members") or {}) >= sect_cap(s):
        service.require(False, f"宗门人数已满（{sect_cap(s)}人），无法再招人。")
    s["pending"].pop(target, None)
    s["members"].setdefault(target, {"role": "帮众", "contribution": 0, "joined_at": int(service.clock())})
    service.store.bind_sect(group, target, sid)
    return f"已同意 {_name(service, target)} 入宗。"


def _reject(service, key, p, qq, s, args):
    service.can_edit(key)
    service.require(_is_officer(_role_of(s, qq)), "只有帮主/长老可审批申请。")
    target = _pid(service, args[0] if args else "")
    if target in (s.get("pending") or {}):
        s["pending"].pop(target, None)
        return f"已拒绝 {_name(service, target)} 的入宗申请。"
    return "没有该成员的待审批申请。"


def _quit(service, group, key, p, qq, s, sid):
    service.can_edit(key)
    service.require(_role_of(s, qq), "你还不是宗门成员。")
    service.require(_role_of(s, qq) != "帮主", "帮主不可退出，请先转让/解散或被免职。")
    s["members"].pop(qq, None)
    service.store.unbind_sect(group, qq)
    return "你已退出宗门。"


def _view(service, s):
    lv = s.get("level", 1)
    members = s.get("members") or {}
    pending = len(s.get("pending") or {})
    building_names = "、".join(_building_name_sorted(s))
    return "\n".join([
        f"## 宗门 · {s['name']}（Lv{lv}）",
        f"帮贡库存 {s.get('treasury', 0)} · 成员 {len(members)}/{sect_cap(s)} · 待审批 {pending}",
        f"活跃度 {int(s.get('activity', 0))}（每 {ACTIVITY_PER_CAP} 点扩招 1 人，封顶 {SECT_CAP_MAX}）",
        f"公告：{s.get('announce') or '（暂无）'}",
        f"建筑：{building_names}",
        f"累计贡献 {s.get('total_contribution', 0)}",
    ])


def _roster(service, s):
    members = sorted((s.get("members") or {}).items(),
                     key=lambda kv: (-int(kv[1].get("contribution", 0))))
    if not members:
        return "宗门暂无成员。"
    return "\n".join(f"- {_name(service, qq)} 【{m['role']}】贡献 {int(m.get('contribution', 0))}"
                     for qq, m in members)


def _roster_all(service, group):
    sects = sorted(service.store.sects_in_group(group),
                   key=lambda x: (-int(x.get("total_contribution", 0)), x.get("name", "")))
    if not sects:
        return "本群暂无宗门。可「创建宗门 名称」立门。"
    lines = [
        f"## 🏛 本群宗门（共 {len(sects)} 个）",
        "> 建宗：`创建宗门 名称`（2000天晶）　加入：`申请入宗 宗名`",
        "",
    ]
    for i, x in enumerate(sects, 1):
        lines.append(f"**{i}. {x['name']}** — Lv{x.get('level', 1)} · {len(x.get('members') or {})}/{sect_cap(x)}人"
                     f" · 贡献 {int(x.get('total_contribution', 0))} · 帮贡 {int(x.get('treasury', 0))}")
    return "\n".join(lines)


def _announce(service, key, p, qq, s, args):
    service.can_edit(key)
    service.require(_role_of(s, qq) == "帮主", "只有帮主可修改公告。")
    text = " ".join(args)
    service.require(len(text) <= 100, "公告最多100字。")
    s["announce"] = text
    return f"宗门公告已更新：{text}"


def _upgrade(service, key, p, qq, s):
    service.can_edit(key)
    service.require(_role_of(s, qq) == "帮主", "只有帮主可升级宗门。")
    lv = s.get("level", 1)
    service.require(lv < SEC_MAX_LEVEL, f"宗门已达最高 Lv{SEC_MAX_LEVEL}。")
    cost = 500 + lv * 1000
    service.require(s.get("treasury", 0) >= cost, f"升级到 Lv{lv+1} 需要 {cost} 帮贡，当前 {s.get('treasury', 0)}。")
    s["treasury"] -= cost
    s["level"] = lv + 1
    return f"宗门升至 Lv{lv+1}！已解锁/强化对应建筑。"


def _donate(service, key, p, a, qq, s, args):
    service.can_edit(key)
    service.require(_role_of(s, qq), "只有宗门成员可捐献。")
    count = 1
    if args and args[0].isdigit():
        count = max(1, int(args[0]))
    have = service.store.get_currency(p, "灵石")
    count = min(count, have)
    contrib = count // 10
    service.require(contrib > 0, f"灵石不足，需至少10灵石（当前 {have}；10灵石=1帮贡）。")
    used = contrib * 10
    service.store.add_currency(p, "灵石", -used)
    _treasury_split(s, qq, contrib)
    return f"捐献灵石×{used}，宗门帮贡 +{contrib}（当前灵石 {have - used}）。"


def _promote(service, key, p, qq, s, args):
    service.can_edit(key)
    service.require(_role_of(s, qq) == "帮主", "只有帮主可封官。")
    target = _pid(service, args[0] if args else "")
    role = args[1] if len(args) > 1 else "长老"
    service.require(target in (s.get("members") or {}), "该成员不在宗门。")
    service.require(role in ("长老", "帮众"), "封官 QQ 长老 / 帮众。")
    service.require(_role_of(s, target) != "帮主", "不可封免帮主。")
    s["members"][target]["role"] = role
    return f"已把 {_name(service, target)} 设为【{role}】。"


def _demote(service, key, p, qq, s, args):
    service.can_edit(key)
    service.require(_role_of(s, qq) == "帮主", "只有帮主可免职。")
    target = _pid(service, args[0] if args else "")
    service.require(target in (s.get("members") or {}), "该成员不在宗门。")
    service.require(_role_of(s, target) != "帮主", "不可免职帮主。")
    s["members"][target]["role"] = "帮众"
    return f"已把 {_name(service, target)} 免职为帮众。"


def _kick(service, group, key, p, qq, s, sid, args):
    service.can_edit(key)
    service.require(_is_officer(_role_of(s, qq)), "只有帮主/长老可踢人。")
    target = _pid(service, args[0] if args else "")
    service.require(target in (s.get("members") or {}), "该成员不在宗门。")
    service.require(_role_of(s, target) != "帮主", "不可踢出帮主。")
    s["members"].pop(target, None)
    s.setdefault("pending", {}).pop(target, None)
    service.store.unbind_sect(group, target)
    return f"已把 {_name(service, target)} 移出宗门。"


def _building_name_sorted(s):
    """已解锁的交互建筑名（按解锁顺序）。"""
    lv = s.get("level", 1)
    names = []
    for bname, cfg in BUILDINGS.items():
        if cfg["kind"] == "interactive" and lv >= cfg["unlock"]:
            names.append(cfg["name"])
    return names


def passive_level(service, group, qq, building):
    """某成员在其所在宗门的被动建筑等级（未加入/未解锁=0，用于纯数值加成）。"""
    _, s = service.store.my_sect(group, qq)
    if not s or not s.get("name"):
        return 0
    b = (s.get("buildings") or {}).get(building, 1)
    return int(b) if int(b) >= BUILDINGS.get(building, {}).get("unlock", 1) else 0


def member_passive(service, group, qq, building):
    """仅宗门成员享受被动加成，非成员返回0。"""
    return passive_level(service, group, qq, building)


def _roll_stamina(service, a):
    """惰性回算修士体力：1点/分钟，醒神丹期间翻倍。"""
    now = int(service.clock())
    stamina_ts = int(a.get("stamina_ts", now))
    elapsed = max(0, now - stamina_ts)
    if elapsed < 60:
        return
    rate = 2 if now < int(a.get("stamina_buff_until", 0)) else 1
    gained = elapsed // 60 * rate
    a["stamina"] = min(a.get("stamina_max", 100), a.get("stamina", 100) + gained)
    a["stamina_ts"] = now


def _spend_stamina(service, a, amount):
    _roll_stamina(service, a)
    if a.get("stamina", 0) < amount:
        return False
    a["stamina"] = a.get("stamina", 0) - amount
    return True


def _settle(service, a, cult, ore):
    a["cultivation"] = a.get("cultivation", 0) + cult
    a["ore"] = a.get("ore", 0) + ore


def _do_mission(service, key, p, a, s, qq):
    if not _role_of(s, qq):
        return "请先「申请入宗」加入宗门。"
    sect_lv = s.get("level", 1)
    if sect_lv < BUILDINGS["mission"]["unlock"]:
        return "宗门等级不足，任务楼尚未开放。"
    if _count(s, qq, "mission") >= DAILY_LIMITS["mission"]:
        return f"今日宗门任务已完成 {DAILY_LIMITS['mission']} 次，明日再来。"
    member = s.setdefault("members", {}).setdefault(qq, {"role": "帮众", "contribution": 0})
    mission = member.get("mission")
    # 有进行中的任务：校验是否已达标，达标即交付领赏（可加时长由任务本身决定，不额外消耗体力）。
    if mission and mission.get("accepted"):
        if mission.get("day") != service.today():
            member.pop("mission", None)
            return "昨日委托已过期（各活动计数已刷新），请重新「宗门任务」接取。"
        key_name, target = mission["key"], mission["target"]
        label, read, cap = _MISSION_SOURCES[key_name]
        prog = min(read(a, s, qq), cap) - mission["base"]
        if prog >= target:
            cult, ore, contrib = _mission_reward(service, s, a)
            _bump(s, qq, "mission")
            _settle(service, a, cult, ore)
            _treasury_split(s, qq, contrib)
            s["activity"] = int(s.get("activity", 0)) + _ACTIVITY["mission"]
            member.pop("mission", None)
            _cd_until(service, a, "mission", _SECT_CD["mission"])
            return (f"任务达成！归还「{label} ×{target}」：修为×{cult} · 灵材×{ore} · 帮贡+{contrib}"
                    f" · 活跃度+{_ACTIVITY['mission']}（今日 {_count(s, qq, 'mission')}/{DAILY_LIMITS['mission']}）。")
        return f"任务进行中：还需完成 {label} ×{target - prog}。完成后再次「宗门任务」归还。"
    # 没有进行中任务：从任务池里接取一个（按剩余余量裁剪，保证必可完成）。
    cd = _cd_wait(service, a, "mission")
    if cd > 0:
        return f"任务楼冷却中，还需 {cd} 秒再接新委托。"
    feasible = []
    for key_name, want in _MISSION_CHOICES:
        label, read, cap = _MISSION_SOURCES[key_name]
        base = read(a, s, qq)
        target = min(want, cap - base)
        if target >= 1:
            feasible.append((key_name, label, base, target))
    if not feasible:
        return ("今日宗门委托均已无法推进——你的历练/试炼已达标，明日刷新后再来。")
    key_name, label, base, target = random.choice(feasible)
    if not _spend_stamina(service, a, 10):
        return "体力不足（接取宗门任务需10体力），可用『体力丹』补充后重试。"
    member["mission"] = {"key": key_name, "label": label, "base": base, "target": target,
                         "accepted": int(service.clock()), "day": service.today()}
    return (f"接取宗门任务：今日完成【{label} ×{target}】！完成后发送「宗门任务」归还领赏。"
            f"（接取消耗10体力，今日还可交付 {DAILY_LIMITS['mission'] - _count(s, qq, 'mission')} 次）")


def _do_explore(service, key, p, a, s, qq):
    if not _role_of(s, qq):
        return "请先「申请入宗」加入宗门。"
    sect_lv = s.get("level", 1)
    if sect_lv < BUILDINGS["north"]["unlock"]:
        return "北秘境需宗门 Lv2 解锁。"
    if _count(s, qq, "explore") >= DAILY_LIMITS["explore"]:
        return f"今日北秘境已探索 {DAILY_LIMITS['explore']} 次，明日再来。"
    cd = _cd_wait(service, a, "explore")
    if cd > 0:
        return f"北秘境冷却中，还需 {cd} 秒。"
    if not _spend_stamina(service, a, 20):
        return "体力不足（探索需20体力）。"
    r = _explore_reward(service, s, a)
    _bump(s, qq, "explore")
    _cd_until(service, a, "explore", _SECT_CD["explore"])
    _settle(service, a, r["cult"], r["ore"])
    if r.get("item"):
        service.store.add_item(p, r["item"], 1)
    _treasury_split(s, qq, r["contrib"])
    s["activity"] = int(s.get("activity", 0)) + _ACTIVITY["explore"]
    return f"探秘北秘境：修为×{r['cult']} · 灵材×{r['ore']} · 帮贡+{r['contrib']} · 活跃度+{_ACTIVITY['explore']}{r.get('bonus', '')}（消耗20体力，今日 {_count(s, qq, 'explore')}/{DAILY_LIMITS['explore']}）。"


def _do_guard(service, key, p, a, s, qq):
    if not _role_of(s, qq):
        return "请先「申请入宗」加入宗门。"
    sect_lv = s.get("level", 1)
    if sect_lv < BUILDINGS["gold"]["unlock"]:
        return "南金库需宗门 Lv3 解锁。"
    if _count(s, qq, "guard") > 0:
        return "今日镇守已完成，明日再来。"
    cd = _cd_wait(service, a, "guard")
    if cd > 0:
        return f"镇守冷却中，还需 {cd} 秒。"
    if not _spend_stamina(service, a, 15):
        return "体力不足（镇守需15体力）。"
    # 修士气血归零=陨落，拦截出战
    cur = a.get("hp")
    if isinstance(cur, int) and cur <= 0:
        ts = a.get("hp_ts", 0)
        if isinstance(ts, int) and (service.clock() - ts) >= 1800:
            mx = __import__('time')
            from .combat import hero_sheet, roll_hp
            mx_val = hero_sheet(a, p)["hp"]
            a["hp"] = max(1, int(mx_val * 0.30))
            a["hp_ts"] = int(service.clock())
            a.pop("hp_dead", None)
        else:
            return "修士已陨落，无法出战。服用『复苏丹』立即复活，或静养30分钟自愈。"
    result = _simulate_guard(service, p, key, s)
    if not result["won"]:
        _bump(s, qq, "guard")
        _cd_until(service, a, "guard", _SECT_CD["guard"])
        s["activity"] = int(s.get("activity", 0)) + _ACTIVITY["guard_loss"]
        return service.record(a, result, "镇守宗门") + "\n镇守失利，明日再战（消耗15体力）。"
    _bump(s, qq, "guard")
    _cd_until(service, a, "guard", _SECT_CD["guard"])
    a["cultivation"] = a.get("cultivation", 0) + (30 + sect_lv * 10)
    a["ore"] = a.get("ore", 0) + 2
    contrib = 50 + 20 * sect_lv
    _treasury_split(s, qq, contrib)
    s["activity"] = int(s.get("activity", 0)) + _ACTIVITY["guard_win"]
    return service.record(a, result, "镇守宗门") + f"\n镇守成功：修为×{30 + sect_lv * 10} · 灵材×2 · 帮贡+{contrib} · 活跃度+{_ACTIVITY['guard_win']}（消耗15体力）。"


def _simulate_guard(service, p, key, s):
    from .combat import build_party, enemies, simulate
    enc = _guard_encounter(service, s)
    return simulate(build_party(p, key), enemies(enc), random.randrange(2 ** 32))


def _exchange_catalog():
    """西仓库可兑换目录：以体力/材料/货币等「非养成主线加速类」为主，避免帮贡直接兑换经验/战力。

    灵石/玄晶/灵材为资源搬运（不直接加战力）；体力类为续航；涤魂散为深渊清理；无经验书/聚灵丹。
    """
    return [
        {"name": "体力丹", "cost": 30, "desc": "恢复30点修士体力"},
        {"name": "扩体散", "cost": 60, "desc": "体力上限永久+20"},
        {"name": "醒神丹", "cost": 50, "desc": "体力回复翻倍1天"},
        {"name": "涤魂散", "cost": 80, "desc": "清除深渊侵蚀5点"},
        {"name": "灵材包", "cost": 50, "desc": "灵材×20"},
        {"name": "灵石袋", "cost": 50, "desc": "灵石×3000"},
        {"name": "玄晶袋", "cost": 60, "desc": "玄晶×500"},
    ]


def _exchange_table(entries):
    lines = ["| 物品 | 帮贡 | 效果 |", "| --- | --- | --- |"]
    for e in entries:
        lines.append(f"| {e['name']} | {e['cost']} | {e['desc']} |")
    lines += ["", "> 发送 `宗门兑换 物品名 数量` 兑换；帮贡来自宗门任务·北秘境·镇守。"]
    return "\n".join(lines)


def _do_exchange(service, key, p, a, s, qq, args):
    if not _role_of(s, qq):
        return "请先「申请入宗」加入宗门。"
    cat = _exchange_catalog()
    item = args[0] if args else ""
    if not item or item in ("目录", "列表", "清单"):
        return _exchange_table(cat)
    count = 1
    if len(args) > 1 and args[1].isdigit():
        count = max(1, int(args[1]))
    entry = next((e for e in cat if e["name"] == item), None)
    if not entry:
        return _exchange_table(cat)
    cost = entry["cost"] * count
    member = s["members"][qq]
    if int(member.get("contribution", 0)) < cost:
        return f"『{item}』需要 {cost} 帮贡，当前 {member.get('contribution', 0)}。"
    member["contribution"] = int(member.get("contribution", 0)) - cost
    if item == "灵石袋":
        service.store.add_currency(p, "灵石", 3000 * count)
    elif item == "玄晶袋":
        service.store.add_currency(p, "玄晶", 500 * count)
    elif item == "灵材包":
        a["ore"] = a.get("ore", 0) + 20 * count
    else:
        # 体力/上限/回复/净化等，进玩家背包后用「使用」生效。
        service.store.add_item(p, item, count)
    return f"已用帮贡兑换『{item}』×{count}（-{cost} 贡献）。"


def _do_star(service, key, p, a, s, qq, args):
    if not _role_of(s, qq):
        return "请先「申请入宗」加入宗门。"
    sect_lv = s.get("level", 1)
    if sect_lv < BUILDINGS["star"]["unlock"]:
        return "星辰阁需宗门 Lv4 解锁。"
    action = args[0] if args else ""
    if not action:
        return "星辰阁：消耗宗门帮贡合成。可用 `星辰阁 星盘大阵`（3天修为翻倍）或 `星辰阁 灵材`（灵材×50）。"
    cd = _cd_wait(service, a, "star")
    if cd > 0:
        return f"星辰阁冷却中，还需 {cd} 秒。"
    if action in ("星盘大阵", "星盘"):
        cost = 300
        if not _pay_treasury(s, cost):
            return f"宗门帮贡不足（需 {cost}）。"
        a["exp_buff_until"] = _extend_buff_until(service, a, 3 * 86400)
        _cd_until(service, a, "star", _SECT_CD["star"])
        return "星盘大阵开光成功：修为翻倍 3 天！"
    if action in ("灵材",):
        cost = 100
        if not _pay_treasury(s, cost):
            return f"宗门帮贡不足（需 {cost}）。"
        a["ore"] = a.get("ore", 0) + 50
        _cd_until(service, a, "star", _SECT_CD["star"])
        return "星辰阁炼材：灵材×50。"
    return "星辰阁：`星辰阁 星盘大阵` / `星辰阁 灵材`。"


def _extend_buff_until(service, a, seconds):
    now = int(service.clock())
    base = max(int(a.get("exp_buff_until", 0)), now)
    result = base + seconds
    a["exp_buff_until"] = result
    return result
