"""Command-only new-account journeys. Clock advances, resources are never injected."""
import json
import random
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from qqbot_pet.petpark.store import PetStore
from qqbot_pet.petpark.adventure.service import AdventureService
from qqbot_pet.petpark.adventure import content as c


def run(seed=20260906):
    rng = random.Random(seed)
    rows = []
    with tempfile.TemporaryDirectory() as tmp, patch('qqbot_pet.petpark.adventure.service.random.randrange', side_effect=rng.randrange):
        path = Path(tmp)/'data.json'
        store = PetStore(path)
        now = [1800000000]
        service = AdventureService(store, lambda: now[0])
        calls = [0]
        def call(job, text):
            calls[0] += 1
            return service.handle('journey', job, text.split())
        def char(job):
            return store.get_player(job,'journey')['adventure']
        for job,pet in zip(c.PROFESSIONS,c.STARTERS):
            assert '请选择职业' in call(job,'创建角色')
            assert '成为' in call(job,'选择职业 '+job)
            assert '结契' in call(job,'结契灵宠 '+pet)
        graduated = set()
        claims = {j:0 for j in c.PROFESSIONS}
        for day in range(1,401):
            now[0] += 86400
            for job in c.PROFESSIONS:
                if job in graduated: continue
                call(job,'修士修炼')
                # Legal equipment purchases, lowest rank first.
                while True:
                    gear = min(c.GEAR,key=lambda name:char(job)['equipment'][c.GEAR[name][0]])
                    if '锻造成功' not in call(job,'锻造 '+gear): break
                while char(job)['level']<80:
                    before = char(job)['level']
                    if before in (10,20,40,60) and char(job)['heaven']==char(job)['realm']:
                        call(job,'灵宠专长 辅助')
                        call(job,'修士配装 守心')
                        call(job,'洞天突破')
                    call(job,'修士突破')
                    if char(job)['level']==before: break
                # Clear available new maps; trial styles cost no reward on defeat.
                for mid,enc in c.MAPS.items():
                    if mid in char(job)['cleared']: continue
                    if char(job)['level']<enc['level']: break
                    call(job,'灵宠专长 '+('辅助' if enc['mechanic']=='burn' else '攻击'))
                    for style in ('破阵','守心','均衡'):
                        call(job,'修士配装 '+style)
                        call(job,'历练 '+mid)
                        if mid in char(job)['cleared']: break
                    if mid not in char(job)['cleared']: break
                # Daily farming through actual successful commands.
                farm = char(job)['cleared'][-1] if char(job)['cleared'] else '1'
                for _ in range(8-char(job)['rewards']):
                    call(job,'历练 '+farm)
                for _ in range(3): call(job,'讨伐首领')
                if '已到账' in call(job,'首领奖励'): claims[job]+=1
                if char(job)['level']==80:
                    call(job,'灵宠专长 辅助')
                    call(job,'修士配装 破阵')
                    call(job,'历练 20 困难')
                    for raid in c.RAIDS:
                        call(job,'组队秘境 '+raid)
                        call(job,'准备出发')
                        call(job,'队伍出发')
                    call(job,'仙途深渊')
                    for _ in range(4):
                        if not char(job)['deep']: break
                        call(job,'深渊抉择 回春')
                    result = call(job,'仙途毕业')
                    if '恭喜' in result:
                        graduated.add(job)
                        rows.append(dict(job=job,days=day,level=char(job)['level'],heaven=char(job)['heaven'],
                                         equipment=dict(char(job)['equipment']),ore=char(job)['ore'],
                                         milestones=list(char(job)['milestones']),graduation=result))
            for job in c.PROFESSIONS:
                if '已到账' in call(job,'首领奖励'): claims[job]+=1
            if day%7==0: print(f'Journey day {day}: '+str({j:char(j)['level'] for j in c.PROFESSIONS}),flush=True)
            # Reopen storage every week, continue the same command journey.
            if day%7==0:
                store=PetStore(path);service=AdventureService(store,lambda:now[0])
            if len(graduated)==3: break
        assert len(graduated)==3, {j:char(j) for j in c.PROFESSIONS if j not in graduated}
        assert all(claims.values()), claims
        now[0]+=86400
        call('剑修','组队秘境 暗月幽林')
        tid,_=service.team_for(service.key('journey','剑修'))
        for job in ('体修','灵修'):
            assert '已加入' in call(job,'加入队伍 '+tid)
        for job in c.PROFESSIONS: assert '已准备' in call(job,'准备出发')
        assert '· 通关' in call('剑修','队伍出发')
        for job in c.PROFESSIONS: assert char(job)['rewards']==1
        assert '已发起' in call('剑修','仙途切磋 体修')
        assert '无损论道' in call('体修','接受切磋')
    return dict(seed=seed,clock='1 claim/day; 12-hour offline cap; simulated days, not retention evidence',
                resource_injection=False,commands=calls[0],world_reward_claims=claims,three_player_raid=True,results=rows)


if __name__=='__main__':
    result=run()
    target=Path(__file__).resolve().parents[1]/'docs'/'adventure-journey-validation.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
