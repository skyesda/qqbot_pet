/* Dedicated administrator profile. Derived portal values never enter saves. */
const PLAYER_PAGE=location.pathname==='/admin/player';
let profile=null, profileDraft=null, profileBase=null, profilePet=0, profileSaving=false;
const profileClone=v=>JSON.parse(JSON.stringify(v));
const heroFields=[['name','道号','text'],['profession','职业','profession'],['gender','性别','gender'],['realm','境界','realm'],['level','等级','number'],['cultivation','修为','number'],['ore','灵材','number'],['heaven','洞天阶序','number'],['spirit_root','灵根','text'],['stamina','体力','number'],['stamina_max','体力上限','number'],['insight','剩余悟性点','number'],['wudao','悟性','number'],['gengu','根骨','number']];
function profileInput(key,label,value,type='number'){
 return `<label class="profile-field">${esc(label)}<input data-profile-field="${escA(key)}" type="${type}" value="${escA(value??'')}" ${type==='number'?'min="0" step="1"':''}></label>`;
}
function profileHeroFields(){
 const adv=profileDraft.adventure||{};
 if(!adv.name)return '<p class="muted">该档案尚未创建修士，可继续管理资产与灵宠。</p>';
 return heroFields.map(([key,label,type])=>{
  if(['profession','gender','realm'].includes(type)){
   const options=type==='profession'?profile.professions:type==='gender'?['男','女']:profile.realms;
   const vals=(options||[]).map((v,i)=>[type==='realm'?i:v,v]);
   if(!vals.some(([v])=>String(v)===String(adv[key])))vals.unshift([adv[key]??'',adv[key]??'未设置']);
   return `<label class="profile-field">${label}<select data-profile-field="adventure.${key}" ${type==='realm'?'data-number="1"':''}>${vals.map(([v,l])=>`<option value="${escA(v)}" ${String(v)===String(adv[key])?'selected':''}>${esc(l)}</option>`).join('')}</select></label>`;
  }
  return profileInput('adventure.'+key,label,adv[key],type);
 }).join('');
}
function profileSet(path,value){const keys=path.split('.');let target=profileDraft;for(const key of keys.slice(0,-1))target=target[key]??={};if(value===undefined)delete target[keys.at(-1)];else target[keys.at(-1)]=value;}
function profileBindInputs(){
 document.querySelectorAll('[data-profile-field]').forEach(el=>{
  el.dataset.initial=el.value;
  const original=el.dataset.profileField.split('.').reduce((v,k)=>v?.[k],profileDraft);
  el.addEventListener('input',()=>{profileSet(el.dataset.profileField,el.value===el.dataset.initial?original:el.type==='number'||el.dataset.number?Number(el.value):el.value);profileMarkDirty();});
 });
}
function profileMarkDirty(){g('profile-status').textContent=profileIsDirty()?'有未保存的修改':'档案已加载';}
function profileIsDirty(){return profileDraft&&JSON.stringify(profileDraft)!==JSON.stringify(profileBase);}
function profilePetForm(){
 const pets=profileDraft.pets||[];
 g('profile-pet-editor').innerHTML=pets.length?`<h3>编辑 · ${esc(pets[profilePet]?.nickname||pets[profilePet]?.species||'灵宠')}</h3>`+buildPetForm(pets[profilePet]):'<p class="muted">这位修士暂无灵宠。</p>';
 if(!pets.length)return;
 g('profile-pet-editor').querySelector('.chk').remove();
 g('profile-pet-editor').querySelectorAll('input,select').forEach(el=>{
  const initial=el.type==='checkbox'?el.checked:el.multiple?JSON.stringify([...el.selectedOptions].map(o=>o.value)):el.value;
  const original=profileDraft.pets[profilePet][el.id.slice(3)];
  el.addEventListener('input',()=>{
   const current=el.type==='checkbox'?el.checked:el.multiple?JSON.stringify([...el.selectedOptions].map(o=>o.value)):el.value;
   const pet=profileDraft.pets[profilePet], key=el.id.slice(3);
   if(current===initial){if(original===undefined)delete pet[key];else pet[key]=original;}
   else pet[key]=el.type==='checkbox'?el.checked:el.multiple?JSON.parse(current):el.type==='number'?Number(el.value):el.value;
   if(['artifact','talent','love_target'].includes(key)&&pet[key]==='')pet[key]=null;
   profileMarkDirty();
   if(['nickname','species','level'].includes(key))profilePetCards();
  });
 });
}
function profilePetCards(){
 const pets=profileDraft.pets||[];
 g('profile-pet-count').textContent=pets.length+' 只';
 g('profile-pets').innerHTML=pets.map((p,i)=>{
  const image=profile.pets?.[i]?.image_url;
  return `<button type="button" class="profile-pet ${i===profilePet?'selected':''}" aria-pressed="${i===profilePet}" onclick="profileSelectPet(${i})">${image?`<img loading="lazy" src="${escA(image)}" alt="">`:'<span class="pet-seal">灵</span>'}<span><b>${esc(p.nickname||p.species||'灵宠')}</b><small>${esc(p.quality||'普通')} · Lv.${esc(p.level||1)}${i===profileDraft.active_pet?' · 当前出战':''}</small></span></button>`;
 }).join('')||'<p class="muted">暂无灵宠</p>';
}
function profileSelectPet(index){if(profileSaving)return;profilePet=index;profilePetCards();profilePetForm();}
function profileRender(){
 const v=profileDraft, hero=profile.role?.adventure||{}, adv=v.adventure||{};
 const key=new URLSearchParams(location.search).get('key')||'';
 const parts=key.split('\x1f');
 g('page-title').textContent=adv.name?adv.name+' · 玩家档案':'玩家档案';
 g('page-description').textContent='群 '+(v.group||parts[0]||'未设置')+' / 用户 '+(v.qq||parts[1]||'未设置');
 g('tablewrap').innerHTML=`<div class="profile-layout"><aside class="profile-roles"><p class="profile-kicker">同一玩家的修士</p>${(profile.roles||[]).map(r=>`<a class="profile-role ${r.key===key?'selected':''}" href="/admin/player?key=${encodeURIComponent(r.key)}"><b>${esc(r.name)}</b><small>群 ${esc(r.group)} · Lv.${esc(r.level)} · 灵宠 ${r.pet_count}</small></a>`).join('')||'<p class="muted">新建档案</p>'}<a class="profile-back" href="/admin">← 返回玩家档案</a></aside><div class="profile-content">
 <section class="profile-panel"><div class="profile-section-heading"><h2>修士档案</h2><span class="tag">${esc(hero.realm||'尚未入道')}</span></div>
 ${hero.portrait_url?`<div class="profile-hero"><div class="profile-loadout">${(hero.equipment||[]).map((e,i)=>`<div class="profile-gear gear-${i}"><img loading="lazy" src="${escA(e.image_url)}" alt=""><span>${esc(e.name)}<small>${esc(e.affix)}</small></span>${profileInput('adventure.equipment.'+e.slot,'装备等级',v.adventure?.equipment?.[e.slot]??0)}</div>`).join('')}<img class="profile-portrait" src="${escA(hero.portrait_url)}" alt="${escA(adv.name)}立绘"></div><div class="profile-hero-info"><p class="profile-kicker">${esc(adv.profession||'修士')} · ${esc(hero.realm||'')} · Lv.${esc(adv.level||1)}</p><h2>${esc(adv.name)}</h2><p>${esc(hero.heaven||'')} · ${esc(hero.spirit_root||'')} · ${esc(hero.element||'')}</p><div class="profile-power"><small>总战力</small><strong>${Number(hero.power||0).toLocaleString('zh-CN')}</strong></div><div class="profile-vitals"><span>气血 ${esc(hero.hp??'—')} / ${esc(hero.hp_max??'—')}</span><span>攻击 ${esc(hero.atk??'—')}</span><span>防御 ${esc(hero.defense??'—')}</span><span>速度 ${esc(hero.speed??'—')}</span></div></div></div>`:''}
 <div class="profile-fields">${profileHeroFields()}</div></section>
 <section class="profile-panel"><div class="profile-section-heading"><h2>修行资产</h2><span class="muted">修士与灵宠共享</span></div><div class="profile-fields">${[['coin','灵石'],['jifen','玄晶'],['diamond','天晶']].map(([k,l])=>profileInput(k,l,v[k]??0)).join('')}${[['battle_win','胜场'],['explore','探索次数']].map(([k,l])=>profileInput('stats.'+k,l,v.stats?.[k]??0)).join('')}</div></section>
 <section class="profile-panel"><div class="profile-section-heading"><h2>全部灵宠</h2><span id="profile-pet-count" class="muted"></span></div><div id="profile-pets" class="profile-pets"></div><div id="profile-pet-editor" class="profile-pet-editor"></div></section>
 <section class="profile-panel"><h2>坐骑</h2><div class="profile-mounts">${(profile.role?.mounts||[]).map(m=>`<div><b>${esc(m.name)}</b><p class="muted">Lv.${esc(m.level)} · 战力 ${esc(m.power)}</p></div>`).join('')||'<p class="muted">暂无坐骑</p>'}</div></section>
 <section class="profile-panel"><h2>背包</h2><datalist id="profile-items">${(META.items||[]).map(i=>`<option value="${escA(i)}">`).join('')}</datalist><div id="profile-bag"></div><button type="button" class="act ghost" id="profile-bag-more" onclick="profileBagMore()">继续显示</button><button type="button" class="act ghost" onclick="profileBagAdd()">＋ 添加物品</button></section>
 <section class="profile-panel"><details><summary>高级编辑 · 完整档案</summary><p class="muted">填写完整 JSON 后点击应用，再保存修改。</p><textarea id="profile-json" aria-label="完整档案 JSON"></textarea><button type="button" class="act ghost" onclick="profileApplyJSON()">应用到当前档案</button></details></section>
 <footer class="profile-savebar"><span id="profile-status" role="status">档案已加载</span><button type="button" class="act ghost" onclick="profileReload()">重新加载</button><button type="button" class="act" id="profile-save" onclick="profileSave()">保存修改</button></footer></div></div>`;
 profileBindInputs();profilePetCards();profilePetForm();profileBagLimit=0;profileBagMore();
 profileBagObserver?.disconnect();profileBagObserver=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)&&!g('profile-bag-more').hidden)profileBagMore();});profileBagObserver.observe(g('profile-bag-more'));
 g('profile-json').value=JSON.stringify(profileDraft,null,2);
 if(!key){g('extrawrap').innerHTML=`<div class="profile-panel"><div class="profile-fields">${profileInput('group','群号',v.group,'text')}${profileInput('qq','用户 ID',v.qq,'text')}</div></div>`;profileBindInputs();}
}
let profileBagLimit=0;
let profileBagObserver=null;
function profileApplyJSON(){try{const value=JSON.parse(g('profile-json').value);if(!value||Array.isArray(value)||typeof value!=='object'||(value.pets&&!Array.isArray(value.pets)))throw new Error('请输入有效的玩家档案对象');profileDraft=value;profilePet=0;profileRender();profileMarkDirty();}catch(error){g('profile-status').textContent=error.message;}}
function profileBagMore(){profileBagLimit+=30;profileBagRender();}
function profileBagRender(){
 const entries=Object.entries(profileDraft.bag||{});
 g('profile-bag').innerHTML=entries.slice(0,profileBagLimit).map(([n,c])=>`<div class="profile-bag-row"><span>${esc(n)}</span><input type="number" min="0" step="1" aria-label="${escA(n)}数量" value="${escA(c)}" data-bag="${escA(n)}"><button type="button" class="act ghost" data-remove-bag="${escA(n)}">移除</button></div>`).join('')||'<p class="muted">背包为空</p>';
 g('profile-bag').querySelectorAll('[data-bag]').forEach(el=>el.oninput=()=>{profileDraft.bag[el.dataset.bag]=Number(el.value);profileMarkDirty();});
 g('profile-bag').querySelectorAll('[data-remove-bag]').forEach(el=>el.onclick=()=>{delete profileDraft.bag[el.dataset.removeBag];profileMarkDirty();profileBagRender();});
 const more=g('profile-bag').nextElementSibling;more.hidden=profileBagLimit>=entries.length;
}
function profileBagAdd(){
 const box=document.createElement('div');box.className='profile-bag-row';box.innerHTML='<input list="profile-items" placeholder="物品名称" aria-label="新物品名称"><input type="number" min="1" step="1" value="1" aria-label="新物品数量"><button class="act" type="button">加入背包</button>';
 box.querySelector('button').onclick=()=>{const inputs=box.querySelectorAll('input'),name=inputs[0].value.trim(),count=Number(inputs[1].value);if(!name||!Number.isInteger(count)||count<=0)return;profileDraft.bag??={};profileDraft.bag[name]=Number(profileDraft.bag[name]||0)+count;profileBagLimit=Object.keys(profileDraft.bag).length;profileMarkDirty();profileBagRender();};g('profile-bag').append(box);
}
async function profileLoad(){
 const key=new URLSearchParams(location.search).get('key');
 if(key){profile=await api('/api/admin/player',{key});if(!profile.ok)throw new Error(profile.msg);profileBase=profileClone(profile.value);}
 else{profile={role:{},pets:[],roles:[]};profileBase={group:'',qq:'',coin:0,jifen:0,diamond:0,pets:[],bag:{}};}
 profileDraft=profileClone(profileBase);profilePet=Math.max(0,Number(profileDraft.active_pet)||0);if(profilePet>=(profileDraft.pets||[]).length)profilePet=0;
 await ensureMeta();profileRender();g('load-state').textContent='已更新';g('request-error').hidden=true;
}
async function profileReload(){if(profileIsDirty()&&!confirm('重新加载会放弃未保存的修改，是否继续？'))return;await profileLoad();}
async function profileSave(){
 if(profileSaving)return;
 if([...g('tablewrap').querySelectorAll('input'),...g('extrawrap').querySelectorAll('input')].some(el=>!el.checkValidity())){g('profile-status').textContent='请检查数值，填写非负整数';return;}
 const key=new URLSearchParams(location.search).get('key')||[profileDraft.group,profileDraft.qq].join('\x1f');
 if(!profileDraft.group||!profileDraft.qq){g('profile-status').textContent='请填写群号和用户 ID';return;}
 profileSaving=true;g('tablewrap').inert=true;g('profile-save').disabled=true;g('profile-status').textContent='正在保存…';
 const existing=Boolean(new URLSearchParams(location.search).get('key'));
 const value=profileClone(profileDraft);if(Array.isArray(value.pets))delete value.pet;
 try{const result=await api('/api/upsert',{table:'players',key,value,profile_edit:true,...(existing?{base:profileBase,require_exists:true}:{create_only:true})});if(!result.ok)throw new Error(result.msg||'保存失败');profileBase=profileClone(profileDraft);if(!existing)history.replaceState(null,'','/admin/player?key='+encodeURIComponent(key));await profileLoad();g('profile-status').textContent='修改已保存';}
 catch(error){g('profile-status').textContent=error.message+'，修改仍保留在当前页面';}
 finally{profileSaving=false;g('tablewrap').inert=false;g('profile-save').disabled=false;}
}
if(PLAYER_PAGE){
 document.body.classList.add('player-profile-page');
 g('section-label').textContent='玩家档案 / 修士详情';g('page-size-note').hidden=true;
 g('addBtn').closest('.bar').hidden=true;
 document.querySelectorAll('.tabs button').forEach(button=>button.onclick=()=>location.href='/admin?tab='+button.dataset.t);
 window.addEventListener('beforeunload',event=>{if(profileIsDirty()){event.preventDefault();event.returnValue='';}});
 profileLoad().catch(reportError);
}
