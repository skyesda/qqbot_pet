"""Super-admin-only group reset with scoped preview, expiring confirmation and backup."""
from copy import deepcopy
import json
import secrets
import time
import uuid

COMMANDS = {"清空本群用户数据", "确认清空本群用户数据", "取消清空本群用户数据"}


def group_keys(store, group):
    return sorted(k for k in store._data['players']
                  if "\x1f" in k and store.resolve_group(k.split("\x1f",1)[0]) == group)


def purge(store, group, actor, expected_keys):
    keys = group_keys(store, group)
    if keys != expected_keys:
        return "本群用户列表已变化，请重新发送「清空本群用户数据」查看人数并确认。"
    before = deepcopy(store._data)
    # Exclusive creation avoids overwriting a previous recovery point.
    folder = store.path.parent / 'group_reset_backups'
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / (uuid.uuid4().hex + '.json')
    with backup.open('x', encoding='utf-8') as f:
        json.dump(before, f, ensure_ascii=False)
    deleted = set(keys)
    def in_group(value):
        return bool(value) and store.resolve_group(str(value)) == group
    try:
        # 玩家/银行是群隔离的；摸金(全局按QQ)、下棋(boardgames.json)、扫雷(全局按QQ)
        # 属跨群共享数据，不应被本群重置清空（2026-09-07 用户反馈修正）。
        for table in ('players','bank_players'):
            values=store._data.get(table,{})
            for key in list(values):
                if '\x1f' in key and in_group(key.split('\x1f',1)[0]): del values[key]
        # 家园是群隔离的（无限服隔离键 hom\x1f<群>\x1f<QQ>），本群重置仅清本群那份；
        # 摸金/扫雷按 QQ 全局共享（tomb_players[qq]/ms_players[qq]，键不带 \x1f 隔离），故显式排除。
        hps=store._data.get('homestead_players',{})
        for key in list(hps):
            parts=str(key).split('\x1f')
            if len(parts)==3 and parts[0]=='hom' and in_group(parts[1]): del hps[key]
        reviews=store._data.get('custom_reviews',{})
        for key,value in list(reviews.items()):
            if in_group(value.get('group')): del reviews[key]
        world=store._data.get('adventure_world',{})
        for tid,team in list(world.get('teams',{}).items()):
            if in_group(team.get('group')) or deleted.intersection(team['members']):
                del world['teams'][tid]
        for key,invite in list(world.get('duels',{}).items()):
            if key in deleted or invite['challenger'] in deleted: del world['duels'][key]
        for key,boss in list(world.get('bosses',{}).items()):
            if key.startswith('infinite:') and in_group(key[len('infinite:'):].rsplit(':',1)[0]):
                del world['bosses'][key]
                continue
            for member in deleted: boss.get('contributions',{}).pop(member,None)
            boss['claimed']=[member for member in boss.get('claimed',[]) if member not in deleted]
        receipts=store._data.get('adventure_receipts',{})
        aliases={k.split('\x1f',1)[0] for k in keys}|{group}
        aliases.update(alias for alias in store._data.get('group_map',{}) if in_group(alias))
        for key in list(receipts):
            if any(key.startswith(alias+':') for alias in aliases): del receipts[key]
        log=store._data.setdefault('group_reset_audit',[])
        log.append(dict(group=group,actor=actor,count=len(keys),time=int(time.time()),backup=backup.name))
        store._data['group_reset_audit']=log[-100:]
        store._flush()
    except Exception:
        store._data=before
        store._restore_pet_refs()
        raise
    return (f"已清空本群 {len(keys)} 名用户的宠物、货币、背包、修士职业、洞天/家园、装备及群内仙途队伍等群隔离数据。\n"
            "已保留备份，编号 "+backup.stem+"。摸金（跨群共享）、下棋、扫雷及群授权/配置/QQ绑定均保留。")


def handle_group_reset(plugin, event, group, tokens):
    if not plugin._is_admin(event):
        return "❌ 仅配置白名单中的大管理员可清空本群用户数据。群主、群管理员和小管理员无此权限。"
    if not plugin._is_group(group):
        return "请在需要清空数据的QQ群内使用，本指令不接受指定其他群。"
    group=plugin.store.resolve_group(str(group))
    actor=str(event.get_sender_id())
    pending=getattr(plugin,'_group_reset_pending',{})
    plugin._group_reset_pending=pending
    now=time.time()
    for key,value in list(pending.items()):
        if value['expires']<=now: del pending[key]
    scope=(group,actor)
    cmd=tokens[0]
    if cmd=='取消清空本群用户数据':
        pending.pop(scope,None)
        return '已取消本次清空。'
    if cmd=='清空本群用户数据':
        if len(tokens)!=1: return '仅支持清空当前群；请发送「清空本群用户数据」。'
        keys=group_keys(plugin.store,group)
        if not keys: return '本群暂无用户档案，无需清空。'
        code=secrets.token_hex(4)
        pending[scope]=dict(code=code,keys=keys,expires=now+300)
        return (f"本群将删除 {len(keys)} 名用户（含管理员自身）的宠物、货币、背包、修士职业、洞天/家园、装备及群内进度。\n"
                "保留群授权、QQ绑定、摸金/下棋/扫雷等跨群共享数据，执行前自动备份。\n"
                f"请由你本人在本群5分钟内发送「确认清空本群用户数据 {code}」。取消：取消清空本群用户数据。")
    request=pending.get(scope)
    if not request or len(tokens)!=2 or not secrets.compare_digest(tokens[1],request['code']):
        return '确认码无效或已过期，请重新发送「清空本群用户数据」。'
    try:
        result=purge(plugin.store,group,actor,request['keys'])
    except OSError:
        return '备份或保存失败，本次清空未完成，请检查存储后重试。'
    if result.startswith('已清空本群'):
        # 摸金(全局按QQ)跨群共享：保留行进中摸金快照，避免本群重置误清用户跨群共有的摸金进度（2026-09-07 反馈修正）。
        # 仅清理本群的行进中扫雷对局；扫雷玩家全局记录保留在 ms_players，不在本群重置范围。
        sessions=getattr(plugin,'_ms_sessions',{})
        for key,value in list(sessions.items()):
            if plugin.store.resolve_group(str(value.get('group_id','')))==group:
                del sessions[key]
    pending.pop(scope,None)
    return result
