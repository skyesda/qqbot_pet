"""Reproducible synthetic playability checks, not live player/retention claims."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from qqbot_pet.petpark.store import PetStore
from qqbot_pet.petpark.adventure.service import AdventureService
from qqbot_pet.petpark.adventure.combat import build_party,enemies,simulate
from qqbot_pet.petpark.adventure import content


def main():
    rows=[]
    with tempfile.TemporaryDirectory() as d:
        store=PetStore(Path(d)/'data.json');service=AdventureService(store)
        for job in content.PROFESSIONS:
            service.handle('g',job,['踏入仙途',job]);p=store.get_player(job,'g');a=p['adventure']
            for map_id in content.MAPS:
                original=content.MAPS[map_id];a['level']=original['level']
                a['heaven']=max(i for i,t in enumerate(content.HEAVENS) if t['level']<=a['level'])
                a['equipment']={k:max(0,original['level']//2) for k in a['equipment']}
                for difficulty,multiplier in [('普通',1),('困难',1.65)]:
                    for style in content.STYLES:
                        a['style']=style;a['pet_role']='辅助' if original['mechanic']=='burn' else '攻击'
                        enc=service.encounter(original,a['heaven']);enc['scale']*=multiplier
                        results=[simulate(build_party(p,job),enemies(enc),seed) for seed in range(30)]
                        rows.append(dict(job=job,map=enc['name'],level=a['level'],style=style,difficulty=difficulty,heaven=a['heaven'],
                                         wins=sum(r['won'] for r in results),runs=len(results),
                                         rounds=round(sum(r['rounds'] for r in results)/len(results),1)))
    target=Path(__file__).resolve().parents[1]/'docs'/'adventure-validation.json'
    target.write_text(json.dumps({'sample':'synthetic normal/hard, free guide pet, half-level equipment, level-eligible personal tier, seeds 0-29','results':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    blocked=[]
    for job in content.PROFESSIONS:
        for m in content.MAPS.values():
            candidates=[r for r in rows if r['job']==job and r['map']==m['name'] and r['difficulty']=='普通']
            if max(r['wins'] for r in candidates)==0:blocked.append((job,m['name']))
    print('Simulations:',sum(r['runs'] for r in rows),'normal maps with no winning style:',blocked)
    if blocked:raise AssertionError(blocked)
    print('Hard-mode strategy examples:')
    for row in rows:
        if row['difficulty']=='困难' and ((row['job']=='体修' and row['map']=='魔之森林') or (row['job']=='灵修' and row['map']=='暗月幽林')):print(row)
    print(target)


if __name__=='__main__':main()
