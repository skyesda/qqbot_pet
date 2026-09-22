
let cur='players', cache={}, editKey=null, editSnapshot=null, META={};
const PAGE_SIZE=10, pages={}, queries={};
let viewEpoch=0, searchTimer=null, metaPromise=null, lastFlagStatus='';
const reads=new Map();
const SECTION_INFO={
 players:['玩家档案','查看玩家与灵宠资料，管理游戏资产。'],groups:['群设置','管理群聊开关、跨群权限与签到信息。'],cards:['卡密管理','生成、导出与管理卡密；勾选仅作用于当前页。'],
 portal_accounts:['网页账号','查看账号绑定的角色与最近登录记录。'],custom_reviews:['定制审核','核对玩家提交的形象与资料，处理定制申请。'],custom_pets:['定制管理','维护已解锁的灵宠与坐骑外观。'],feedbacks:['玩家反馈','查看问题详情，回复玩家的建议与反馈。'],
 events:['活动配置','管理活动时间、玩法、奖励与商店。'],lottery:['口令抽奖','设置参与口令、奖品与开奖时间。'],push:['群推送','编辑群消息，查看和管理发送计划。'],celebrate:['生辰盛典','配置开奖场次、奖品库存与瓜分规则。'],zhongyuan:['中元活动','管理活动时段、解密与奖励配置。'],assistant_free:['免费助手','设置全服自动助手的免费使用时段。'],audit:['数据追溯','按玩家、群聊或时间查询记录，处理异常标记。'],app_release:['App 发布','维护安卓客户端版本与安装包。']
};
const LIST_TABS=new Set(['players','groups','cards','events','portal_accounts','custom_reviews','custom_pets','feedbacks']);
const READ_PATHS=new Set(['/api/list','/api/portal_accounts','/api/custom_reviews','/api/custom_pets','/api/custom_mounts','/api/feedbacks','/api/app_release/info','/api/lottery/state','/api/zhongyuan/config','/api/zhongyuan/data','/api/push/state','/api/celebrate/state','/api/assistant_free/state','/api/audit/query','/api/audit/flags']);
function pageState(key){return pages[key]||(pages[key]={page:1,total:0,pages:1});}
function resetPage(key){pageState(key).page=1;}
function updateHeading(key){
 const [title,description]=SECTION_INFO[key];
 g('page-title').textContent=title;g('section-label').textContent=title;g('page-description').textContent=description;
 g('page-size-note').hidden=!LIST_TABS.has(key);g('load-state').textContent='正在加载';
 document.querySelectorAll('.tabs button').forEach(b=>{if(b.dataset.t===key)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
 const menu=document.querySelector('.menu-button');if(menu)menu.setAttribute('aria-expanded','false');
}
function toggleNavigation(){const open=document.body.classList.toggle('navigation-open');document.querySelector('.menu-button').setAttribute('aria-expanded',String(open));}
function cancelReads(){for(const controller of reads.values())controller.abort();reads.clear();}
function reportError(error){
 if(error?.name==='AbortError')return;
 const banner=g('request-error');if(!banner)return;
 banner.hidden=false;banner.textContent=error?.message||'加载失败，请检查连接后重试。';
 const retry=document.createElement('button');retry.className='act ghost';retry.textContent='重新加载';retry.onclick=()=>load().catch(reportError);banner.append(' ',retry);
 g('load-state').textContent='加载失败';g('tablewrap').removeAttribute('aria-busy');
}
window.addEventListener('unhandledrejection',event=>{event.preventDefault();reportError(event.reason);});
async function requestJSON(path,body={},method='POST',managed=true){
 const read=managed&&READ_PATHS.has(path), epoch=viewEpoch;
 const lane=path+(body.key!==undefined?':detail':body.export?':export':'');
 const controller=new AbortController();
 if(read){reads.get(lane)?.abort();reads.set(lane,controller);g('tablewrap').setAttribute('aria-busy','true');g('load-state').textContent='正在加载';g('request-error').hidden=true;}
 const timer=setTimeout(()=>controller.abort('timeout'),20000);
 try{
  const options={method,signal:controller.signal,headers:{Accept:'application/json'}};
  if(method==='GET')path+='?'+new URLSearchParams(body);
  else{options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
  const response=await fetch(path,options);
  if(read&&(epoch!==viewEpoch||reads.get(lane)!==controller))throw new DOMException('Stale request','AbortError');
  if(response.status===401||response.status===403||(response.redirected&&new URL(response.url).pathname==='/login')){location.assign('/login');throw new DOMException('Session expired','AbortError');}
  if(!response.ok)throw new Error('请求失败（'+response.status+'），请稍后重试。');
  const result=await response.json();
  if(read&&(epoch!==viewEpoch||reads.get(lane)!==controller))throw new DOMException('Stale request','AbortError');
  if(read&&!result.ok)throw new Error(result.msg||'加载失败，请稍后重试。');
  return result;
 }catch(error){
  if(controller.signal.reason==='timeout')error=new Error('请求超时，请检查连接后重试。');
  if(!read||epoch===viewEpoch){if(error.name!=='AbortError')reportError(error);}
  throw error;
 }finally{
  clearTimeout(timer);
  if(read&&reads.get(lane)===controller)reads.delete(lane);
  if(epoch===viewEpoch&&!reads.size){g('tablewrap').removeAttribute('aria-busy');if(g('request-error').hidden)g('load-state').textContent='已更新';}
 }
}
async function readPage(path,extra,key,method='POST'){
 const state=pageState(key);
 const result=await requestJSON(path,{...extra,page:state.page,size:PAGE_SIZE,q:g('q').value.trim()},method);
 Object.assign(state,{page:result.page,total:result.total,pages:result.pages,stats:result.stats});
 return result;
}
function pagerHTML(key){
 const s=pageState(key), total=s.total||0, page=s.page||1, last=s.pages||1;
 const from=total?(page-1)*PAGE_SIZE+1:0,to=Math.min(page*PAGE_SIZE,total);
 return `<nav class="pagination" aria-label="${escA(SECTION_INFO[key]?.[0]||'列表')}分页">
  <span class="page-summary">第 ${from}–${to} 条，共 ${total} 条<span class="page-rule">每页 10 条</span></span>
  <div class="page-controls"><button class="act ghost" ${page<=1?'disabled':''} onclick="changePage('${key}',1)" aria-label="首页">«</button><button class="act ghost" ${page<=1?'disabled':''} onclick="changePage('${key}',${page-1})">上一页</button><span class="page-position">${page} / ${last}</span><button class="act ghost" ${page>=last?'disabled':''} onclick="changePage('${key}',${page+1})">下一页</button><button class="act ghost" ${page>=last?'disabled':''} onclick="changePage('${key}',${last})" aria-label="末页">»</button></div>
 </nav>`;
}
function showPager(key){const s=pageState(key);g('count').textContent='共 '+(s.total||0)+' 条';g('pager').innerHTML=pagerHTML(key);}
async function changePage(key,page){
 const s=pageState(key);s.page=Math.max(1,Math.min(s.pages||1,page));
 if(key==='audit_flags')return auditFlags();
 await load();
}
function scheduleSearch(){
 clearTimeout(searchTimer);
 queries[cur]=g('q').value;
 // Invalidate the old query immediately, not after the debounce delay.
 viewEpoch++;cancelReads();
 searchTimer=setTimeout(()=>{resetPage(cur);if(cur==='custom_pets')resetPage('custom_mounts');load().catch(reportError);},220);
}
function ensureMeta(){if(!metaPromise)metaPromise=loadMeta().catch(error=>{metaPromise=null;throw error;});return metaPromise;}

// Small editable configuration tables stay in the DOM: paging must never drop
// unsaved values from event/stock forms when the administrator clicks Save.
const formPages={};
function paginateFormTables(){
 g('tablewrap').querySelectorAll('table').forEach((table,index)=>{
  const rows=[...table.querySelectorAll('tbody > tr')];
  if(!rows.length)return;
  const key=cur+':'+index,total=rows.length,last=Math.max(1,Math.ceil(total/PAGE_SIZE));
  const page=formPages[key]=Math.min(formPages[key]||1,last);
  rows.forEach((row,i)=>row.hidden=i<(page-1)*PAGE_SIZE||i>=page*PAGE_SIZE);
  let bar=table.nextElementSibling;
  if(!bar?.classList.contains('form-pagination')){bar=document.createElement('div');bar.className='pagination form-pagination';table.after(bar);}
  bar.innerHTML=`<span>共 ${total} 条 · 每页 10 条</span><div class="page-controls"><button type="button" class="act ghost" ${page===1?'disabled':''} onclick="formPage('${key}',-1)">上一页</button><span>${page} / ${last}</span><button type="button" class="act ghost" ${page===last?'disabled':''} onclick="formPage('${key}',1)">下一页</button></div>`;
 });
}
function formPage(key,delta){formPages[key]=Math.max(1,(formPages[key]||1)+delta);paginateFormTables();}
document.addEventListener('keydown',event=>{if(event.key==='Escape'){document.querySelectorAll('.modal').forEach(m=>m.style.display='none');document.body.classList.remove('navigation-open');document.querySelector('.menu-button')?.setAttribute('aria-expanded','false');}});

let paCache=[];
let LOTTERY=null;

async function loadPortalAccounts(){
 const r=await readPage('/api/portal_accounts',{},'portal_accounts','GET');
 paCache=r.data||[];
 renderPortalAccounts();
}
function renderPortalAccounts(){
 const q=(document.getElementById('q').value||'').toLowerCase();
 let rows='';
 for(const a of paCache){
  rows+=`<tr>
   <td class="k">${esc(a.id)}</td>
   <td class="num">${esc(a.qq||'')}</td>
   <td class="num">${(a.bound_slots||[]).length}</td>
   <td class="muted">${fdate(a.last_login)}</td>
   <td class="muted">${fdate(a.created_at)}</td>
   <td style="white-space:nowrap"><button class="act" onclick='paDetail(${tj(a.id)})'>查看</button> <button class="act" onclick='paResetPwd(${tj(a.id)})'>重置密码</button> <button class="act del" onclick='paDelete(${tj(a.id)})'>删除</button></td>
  </tr>`;
 }
 document.getElementById('count').textContent='共 '+paCache.length+' 个账号';
 document.getElementById('extrawrap').innerHTML='';
 document.getElementById('tablewrap').innerHTML = rows
   ? `<table><thead><tr><th>ID</th><th>QQ</th><th>绑定角色</th><th>最后登录</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无网页账号</div>`;
 showPager('portal_accounts');
}
function paDetail(aid){
 const a=paCache.find(x=>x.id===aid); if(!a) return;
 const slots=(a.bound_slots||[]).slice();
 const bps=a.bound_pets||[];
 let pets='';
 for(const s of slots){
  const bp=bps.find(p=>p.group===s.group&&p.qq===s.qq);
  const petTxt=bp?` · ${esc(bp.nickname||'未命名')}`:'';
  pets+=`<div class="row" style="align-items:center;margin:6px 0;padding:8px;border:1px solid #d8d7c9;border-radius:4px">
   <div style="flex:1"><div class="muted">群号 / 用户ID</div>${esc(s.group||'')} / ${esc(s.qq||'')}</div>
   <div style="flex:1"><div class="muted">角色</div>修士/坐骑${petTxt?' · 宠物'+petTxt:''}</div>
   <div><button class="act del" onclick='paUnbind(${tj(aid)},${tj(s.group)},${tj(s.qq)})'>解绑</button></div>
  </div>`;
 }
 if(!pets) pets='<div class="muted">未绑定任何角色</div>';
 g('patitle').textContent='账号详情：'+esc(a.qq||a.id);
 g('pabody').innerHTML=`
  <div class="row"><div><label class="fld">ID</label><input readonly value="${esc(a.id)}"></div><div><label class="fld">QQ</label><input readonly value="${esc(a.qq||'')}"></div></div>
  <div class="sec">已绑定角色（群号 / 用户ID）</div>${pets}`;
 g('pamodal').style.display='flex';
}
function closePaModal(){ g('pamodal').style.display='none'; }
async function paResetPwd(aid){
 const pwd=prompt('请输入新密码（至少6位）：'); if(!pwd) return;
 if(pwd.length<6){ alert('密码长度至少 6 位'); return; }
 const r=await api('/api/portal_accounts/reset_password',{account_id:aid,new_password:pwd});
 alert(r.ok?(r.msg||'重置成功'):(r.msg||'重置失败'));
}
async function paDelete(aid){ if(!confirm('确认删除账号 '+aid+'？绑定关系也会清空。')) return; await api('/api/portal_accounts/delete',{account_id:aid}); loadPortalAccounts(); }
function crImgUrl(img){ if(!img) return ''; if(img.startsWith('http') || img.startsWith('/')) return esc(img); return '/api/admin/image?file='+encodeURIComponent(img); }
function crImgBox(img,label){
 if(!img) return '';
 const u = crImgUrl(img);
 return `<div class="cr-imgbox">
   <div class="muted">${label}</div>
   <a href="${u}" target="_blank" title="点击查看原图">
     <img loading="lazy" decoding="async" src="${u}" onerror="this.onerror=null;this.replaceWith(Object.assign(document.createElement('div'),{className:'img-missing',innerHTML:'<div> 图片缺失</div><small>该定制图文件可能已被清理；建议让玩家重新提交一次。</small>'}));">
   </a>
 </div>`;
}
async function paUnbind(aid,group,qq){ if(!confirm(`确认解绑 ${group} / ${qq}？`)) return; await api('/api/portal_accounts/unbind',{account_id:aid,group, qq}); loadPortalAccounts(); paDetail(aid); }

let crCache=[], crStatus='pending', crKind='';
async function loadCustomReviews(status=crStatus, kind=crKind){
 if(status!==crStatus || kind!==crKind) resetPage('custom_reviews');
 crStatus=status; crKind=kind;
 const r=await readPage('/api/custom_reviews',{status, kind},'custom_reviews');
 crCache=r.data||[];
 renderCustomReviews();
}
function renderCustomReviews(){
 const q=(document.getElementById('q').value||'').toLowerCase();
 let rows='';
 for(const r of crCache){
  const isMount = r.kind==='mount';
  const isMImg = r.kind==='mount_image';   // 坐骑换装：旧+新图对照，均可驳回回收
  const oldImg = r.old&&r.old.image||'';
  const newImg = r.new&&r.new.image||'';
  const kindTag = `<span class="tag ${isMount||isMImg?'':'off'}" style="margin-right:4px">${isMount?'坐骑':(isMImg?'坐骑换装':'宠物')}</span>`;
  const nameCell = isMount
    ? `<div><b>坐骑外观</b>${esc(r.mount_name||'')?`<div>坐骑：${esc(r.mount_name)}</div>`:''}</div>`
    : (isMImg
      ? `<div><b>坐骑外观更换</b>${esc(r.mount_name||'')?`<div>坐骑：${esc(r.mount_name)}</div>`:''}</div>`
      : (r.new.species_name?`<div class="muted">旧：${esc(r.old.species_name||'')}</div><div>新：${esc(r.new.species_name||'')}</div>`:'—'));
  rows+=`<tr>
   <td class="k">${esc(r.id)}</td>
   <td class="num">${esc(r.qq||'')}</td>
   <td class="num">${esc(r.group||'')}</td>
   <td>${kindTag}${nameCell}</td>
   <td>${(newImg||(!isMount&&oldImg))?`<div class="cr-imgrow">${isMount?'':crImgBox(oldImg,'旧')}${crImgBox(newImg, isMount?'新外观':'新')}</div>`:'—'}</td>
   <td class="muted">${fdate(r.created_at)}</td>
   <td>${r.status==='pending'?`<button class="act" onclick='crApprove(${tj(r.id)})'>通过</button> <button class="act del" onclick='crReject(${tj(r.id)})'>拒绝</button>`:`<span class="tag ${r.status==='approved'?'on':'off'}">${r.status==='approved'?'已通过':'已拒绝'}</span><div class="muted">${esc(r.reason||'')}</div>`}</td>
  </tr>`;
 }
 document.getElementById('count').textContent='共 '+crCache.length+' 条';
 document.getElementById('extrawrap').innerHTML=`
  <div class="bar" style="margin-bottom:8px">
   <button class="act ${crStatus==='pending'?'':'ghost'}" onclick="loadCustomReviews('pending')">待审核</button>
   <button class="act ${crStatus==='approved'?'':'ghost'}" onclick="loadCustomReviews('approved')">已通过</button>
   <button class="act ${crStatus==='rejected'?'':'ghost'}" onclick="loadCustomReviews('rejected')">已拒绝</button>
   <button class="act ${crStatus===''?'':'ghost'}" onclick="loadCustomReviews('')">全部</button>
   <span style="margin:0 6px" class="muted">类型</span>
   <button class="act ${crKind===''?'':'ghost'}" onclick="loadCustomReviews(crStatus,'')">全部</button>
   <button class="act ${crKind==='pet'?'':'ghost'}" onclick="loadCustomReviews(crStatus,'pet')">宠物</button>
   <button class="act ${crKind==='mount'?'':'ghost'}" onclick="loadCustomReviews(crStatus,'mount')">坐骑</button>
   <button class="act ${crKind==='mount_image'?'':'ghost'}" onclick="loadCustomReviews(crStatus,'mount_image')">坐骑换装</button>
  </div>`;
 document.getElementById('tablewrap').innerHTML = rows
   ? `<table><thead><tr><th>ID</th><th>QQ</th><th>群号</th><th>类型 / 名称</th><th>图片</th><th>提交时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无审核记录</div>`;
 showPager('custom_reviews');
}
async function crApprove(id){ if(!confirm('确认通过该定制申请？')) return; const r=await api('/api/custom_reviews/approve',{id}); alert(r.ok?(r.msg||'已通过'):(r.msg||'操作失败')); loadCustomReviews(crStatus); }
async function crReject(id){ const reason=prompt('请输入拒绝原因：'); if(!reason) return; const r=await api('/api/custom_reviews/reject',{id,reason}); alert(r.ok?(r.msg||'已拒绝'):(r.msg||'操作失败')); loadCustomReviews(crStatus); }

let cpCache=[], cmCache=[];
async function loadCustomPets(){
 const [r,m]=await Promise.all([readPage('/api/custom_pets',{},'custom_pets'),readPage('/api/custom_mounts',{},'custom_mounts')]);
 cpCache=r.data||[];
 cmCache=m.data||[];
 renderCustomPets();
}
function renderCustomPets(){
 const q=(document.getElementById('q').value||'').toLowerCase();
 let rows='';
 for(const p of cpCache){
  const img=p.custom_image?`<img loading="lazy" decoding="async" src="/custom_images/${esc(p.custom_image)}" style="width:64px;height:64px;object-fit:cover;border-radius:4px;border:1px solid #d8d7c9">`:'—';
  const tags=(p.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join(' ');
  rows+=`<tr>
   <td class="num">${esc(p.group)}</td>
   <td class="num">${esc(p.qq)}</td>
   <td class="num">${esc(p.account_qq)}</td>
   <td>${esc(p.nickname)}</td>
   <td>${esc(p.custom_species_name||p.species)}</td>
   <td>${esc(p.quality)}</td>
   <td>${tags}</td>
   <td>${img}</td>
   <td style="white-space:nowrap"><button class="act del" onclick='cpCancel(${tj(p.group)},${tj(p.qq)})'>取消定制</button></td>
  </tr>`;
 }
 document.getElementById('count').textContent='共 '+cpCache.length+' 个宠物 / '+cmCache.length+' 个坐骑';
 document.getElementById('extrawrap').innerHTML='';
 const petHtml = rows
   ? `<table><thead><tr><th>群号</th><th>用户ID</th><th>账号QQ</th><th>宠物昵称</th><th>种类名称</th><th>品质</th><th>标签</th><th>定制图</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无已解锁定制的宠物</div>`;
 let mrows='';
 cmCache.forEach((m,i)=>{
  const img=m.custom_image?`<img loading="lazy" decoding="async" src="/custom_images/${esc(m.custom_image)}" onerror="this.replaceWith(document.createTextNode('（图缺失）'))" style="width:64px;height:64px;object-fit:cover;border-radius:4px;border:1px solid #d8d7c9">`:'（未设置）';
  mrows+=`<tr>
   <td class="num">${esc(m.group)}</td>
   <td class="num">${esc(m.qq)}</td>
   <td class="num">${esc(m.account_qq)}</td>
   <td>${esc(m.name)}</td>
   <td class="num">${esc(String(m.power))}</td>
   <td>${esc(m.plate||'')}</td>
   <td>${img}</td>
   <td style="white-space:nowrap"><input type="file" id="cm_file_${i}" accept=".jpg,.jpeg,.png,.gif,.webp" style="width:190px"> <button class="act" onclick="cmSetImage(${i})">更换外观图</button></td>
  </tr>`;
 });
 const mountHtml = `<h3 style="margin:22px 0 8px">定制坐骑</h3>` + (mrows
   ? `<table><thead><tr><th>群号</th><th>用户ID</th><th>账号QQ</th><th>坐骑名</th><th>战力</th><th>号牌</th><th>外观图</th><th>操作</th></tr></thead><tbody>${mrows}</tbody></table>`
   : `<div class="empty">暂无定制坐骑</div>`);
 document.getElementById('tablewrap').innerHTML = '<h2 class=table-title>定制灵宠</h2>'+petHtml+pagerHTML('custom_pets')+mountHtml+pagerHTML('custom_mounts');
 g('pager').innerHTML='';
 g('count').textContent='宠物 '+pageState('custom_pets').total+' 条 / 坐骑 '+pageState('custom_mounts').total+' 条';
}
async function cmSetImage(i){
 const m=cmCache[i];
 const inp=document.getElementById('cm_file_'+i);
 if(!inp||!inp.files.length){alert('请先选择图片文件');return;}
 if(!confirm('确认将坐骑「'+m.name+'」的外观图替换为所选图片？（旧图将被回收）')) return;
 const fd=new FormData();
 fd.append('group',m.group); fd.append('qq',m.qq); fd.append('name',m.name); fd.append('image',inp.files[0],inp.files[0].name);
 try{
  const r=await (await fetch('/api/custom_mounts/set_image',{method:'POST',body:fd})).json();
  alert(r.ok?(r.msg||'已更新'):(r.msg||'更新失败'));
  if(r.ok) loadCustomPets();
 }catch(e){ alert('上传失败：'+e); }
}
function fsize(n){n=Number(n)||0;if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';return (n/1048576).toFixed(2)+' MB';}
async function loadAppRelease(){
 document.getElementById('count').textContent='';
 document.getElementById('extrawrap').innerHTML='';
 let rel={};
 const r=await api('/api/app_release/info',{}); rel=r.data||{};
 const cur=rel.filename?`当前线上版本：<b>${esc(rel.version_name||'')}</b>（versionCode ${esc(rel.version_code||0)}），文件 ${esc(rel.filename)}（${fsize(rel.size)}），发布于 ${fdate(rel.updated_at)}`:'当前尚未发布任何版本。';
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:640px">
  <div class="muted" style="margin-bottom:14px;line-height:1.7">${cur}</div>
  <div style="background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px;padding:20px">
   <h3 style="margin:0 0 14px">发布新版本</h3>
   <div style="display:flex;flex-direction:column;gap:12px">
    <label>版本号 versionCode（必须比当前大的整数）<input id="ar_code" type="number" placeholder="如 2" style="width:100%;margin-top:5px;padding:9px 12px;border:1px solid #d8d7c9;border-radius:3px" value="${esc((rel.version_code||0)+1)}"></label>
    <label>版本名 versionName（展示给用户，如 1.0.1）<input id="ar_name" placeholder="如 1.0.1" style="width:100%;margin-top:5px;padding:9px 12px;border:1px solid #d8d7c9;border-radius:3px"></label>
    <label>更新说明（可选，多行）<textarea id="ar_log" rows="4" placeholder="本次更新内容…" style="width:100%;margin-top:5px;padding:9px 12px;border:1px solid #d8d7c9;border-radius:3px;resize:vertical"></textarea></label>
    <label>APK 文件（不选则仅更新版本信息）<input id="ar_apk" type="file" accept=".apk,application/vnd.android.package-archive" style="margin-top:5px"></label>
    <div><button class="act" onclick="uploadApp()">发布</button> <span class="muted" id="ar_msg"></span></div>
   </div>
  </div>
 </div>`;
}
async function uploadApp(){
 const code=document.getElementById('ar_code').value.trim();
 const name=document.getElementById('ar_name').value.trim();
 const log=document.getElementById('ar_log').value.trim();
 const f=document.getElementById('ar_apk').files[0];
 const msg=document.getElementById('ar_msg');
 if(!code||Number(code)<=0){msg.textContent='请填写正确的版本号';return;}
 if(!name){msg.textContent='请填写版本名';return;}
 const fd=new FormData();
 fd.append('version_code',code); fd.append('version_name',name); fd.append('changelog',log);
 if(f) fd.append('apk',f,f.name);
 msg.textContent='上传中…';
 try{
  const r=await (await fetch('/api/app_release/upload',{method:'POST',body:fd})).json();
  msg.textContent=r.msg||(r.ok?'发布成功':'发布失败');
  if(r.ok) setTimeout(loadAppRelease,800);
 }catch(e){ msg.textContent='上传失败：'+e; }
}
let fbCache=[], fbStatus='pending';
async function loadFeedbacks(status){
 if(status===undefined) status=fbStatus;
 if(status!==fbStatus) resetPage('feedbacks');
 fbStatus=status;
 const r=await readPage('/api/feedbacks',{status},'feedbacks');
 fbCache=r.data||[];
 renderFeedbacks();
}
function renderFeedbacks(){
 const q=(document.getElementById('q').value||'').toLowerCase();
 let rows='';
 for(const f of fbCache){
  const imgs=(f.images||[]).map(im=>`<a href="/feedback_images/${esc(im)}" target="_blank"><img loading="lazy" decoding="async" src="/feedback_images/${esc(im)}" style="width:64px;height:64px;object-fit:cover;border-radius:4px;border:1px solid #d8d7c9"></a>`).join(' ')||'—';
  const meta=f.kind==='bug'?`<div class="muted">发生时间：${esc(f.occur_time||'—')}</div><div class="muted">群号：${esc(f.group||'—')} · 用户ID：${esc(f.user_id||'—')}</div>`:'';
  rows+=`<tr>
   <td><span class="tag ${f.kind==='bug'?'off':'on'}" onclick='fbDetail(${tj(f.id)})' style="cursor:pointer" title="查看详情">${f.kind==='bug'?'Bug':'建议'}</span></td>
   <td class="num">${esc(f.qq||'')}</td>
   <td style="max-width:340px"><div class="content-ellipsis" title="${esc(f.content).replace(/"/g,'&quot;')}">${esc(f.content)}</div>${meta}</td>
   <td>${imgs}</td>
   <td class="muted">${fdate(f.created_at)}</td>
   <td>${f.status==='pending'?`<span class="tag off">待处理</span>`:`<span class="tag on">已回复</span><div class="muted" style="max-width:220px;white-space:pre-wrap">${esc(f.reply||'')}</div><div class="muted">${fdate(f.replied_at)}</div>`}</td>
   <td style="white-space:nowrap"><button class="act" onclick='fbDetail(${tj(f.id)})'>详情</button> <button class="act" onclick='fbReply(${tj(f.id)})'>${f.status==='pending'?'回复':'修改回复'}</button> <button class="act del" onclick='fbDelete(${tj(f.id)})'>删除</button></td>
  </tr>`;
 }
 document.getElementById('count').textContent='共 '+fbCache.length+' 条';
 document.getElementById('extrawrap').innerHTML=`
  <div class="bar" style="margin-bottom:8px">
   <button class="act ${fbStatus==='pending'?'':'ghost'}" onclick="loadFeedbacks('pending')">待处理</button>
   <button class="act ${fbStatus==='resolved'?'':'ghost'}" onclick="loadFeedbacks('resolved')">已回复</button>
   <button class="act ${fbStatus===''?'':'ghost'}" onclick="loadFeedbacks('')">全部</button>
  </div>`;
 document.getElementById('tablewrap').innerHTML = rows
   ? `<table><thead><tr><th>类型</th><th>账号QQ</th><th>内容</th><th>截图</th><th>提交时间</th><th>处理状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无反馈</div>`;
 showPager('feedbacks');
}
async function fbReply(id){
 const cur=fbCache.find(x=>x.id===id)||{};
 const reply=prompt('回复内容（用户将在「我的反馈」中看到）：', cur.reply||'');
 if(!reply) return;
 const r=await api('/api/feedbacks/reply',{id,reply});
 alert(r.ok?(r.msg||'已回复'):(r.msg||'操作失败'));
 loadFeedbacks(fbStatus);
}
async function fbDelete(id){ if(!confirm('确认删除该条反馈？')) return; const r=await api('/api/feedbacks/delete',{id}); alert(r.ok?'已删除':(r.msg||'操作失败')); loadFeedbacks(fbStatus); }
function fbDetail(id){
 const f=fbCache.find(x=>x.id===id)||{};
 const meta=f.kind==='bug'
  ? `<div class="fb-detail-meta">发生时间：${esc(f.occur_time||'—')}　|　群号：${esc(f.group||'—')}　|　用户ID：${esc(f.user_id||'—')}</div>`
  : `<div class="fb-detail-meta">群号：${esc(f.group||'—')}　|　用户ID：${esc(f.user_id||'—')}</div>`;
 const imgs=(f.images||[]).map(im=>`<a href="/feedback_images/${esc(im)}" target="_blank"><img loading="lazy" decoding="async" src="/feedback_images/${esc(im)}"></a>`).join('')||'';
 const reply=f.reply?`<div style="margin-top:18px;padding:14px;background:#f1efe3;border-radius:6px"><div class="muted" style="font-weight:700;margin-bottom:6px">管理员回复（${fdate(f.replied_at)}）</div><div style="white-space:pre-wrap">${esc(f.reply)}</div></div>`:'';
 g('fbtitle').textContent=(f.kind==='bug'?' Bug 反馈':' 玩家建议')+' 详情';
 g('fbbody').innerHTML=`
  <div class="fb-detail-meta">提交账号：${esc(f.qq||'—')}　|　提交时间：${fdate(f.created_at)}</div>
  ${meta}
  <div class="fb-detail-content" style="margin-top:14px">${esc(f.content)}</div>
  ${imgs?`<div class="fb-detail-images">${imgs}</div>`:''}
  ${reply}
 `;
 g('fbmodal').style.display='flex';
}
function closeFbModal(){ g('fbmodal').style.display='none'; }
g('fbmodal').addEventListener('click',e=>{ if(e.target===g('fbmodal')) closeFbModal(); });

async function cpCancel(group,qq){ if(!confirm('确认取消该宠物的定制权限？将移除定制图和自定义名称。')) return; const r=await api('/api/custom_pets/cancel',{group,qq}); alert(r.ok?(r.msg||'已取消'):(r.msg||'操作失败')); loadCustomPets(); }

const PET_FIELDS=[
 ['nickname','昵称','text'],['species','种类','sel','species'],
 ['quality','品质','sel','qualities'],['element','元素','sel','elements'],
 ['gender','性别','sel','genders'],['stage','阶段','sel','stages'],
 ['level','等级','num'],['exp','经验','num'],
 ['hp','生命','num'],['hp_max','生命上限','num'],
 ['atk','攻击','num'],['def','防御','num'],['intel','智力','num'],
 ['mood','心情(1-5)','num'],['energy','精力','num'],['energy_max','精力上限','num'],
 ['status','状态','sel','statuses'],['love_state','姻缘','sel','love_states'],
 ['love_target','伴侣键(群+QQ)','text'],['favor','好感度','num'],
 ['artifact','神器','sel','artifacts','无'],['talent','天赋','sel','talents','无'],
];
const PET_DEF={nickname:'宝宝',species:'幼龙',quality:'普通',element:'金',gender:'男',stage:'幼年期',level:1,exp:0,hp:800,hp_max:800,atk:50,def:40,intel:30,mood:5,energy:100,energy_max:100,status:'正常',love_state:'单身',love_target:null,favor:0,artifact:null,talent:null,custom:false,skills:[],ascended:false,frozen_until:0};
async function loadMeta(){const r=await requestJSON('/api/meta',{},'POST',false);META=r.data||{};const am=g('amt_item');if(am)am.innerHTML=optHtml(META.items||[],'','道具名（可选）');}
function escA(s){return esc(s).replace(/"/g,'&quot;');}
function optHtml(list,val,empty){let h='';const L=(list||[]).map(String);if(empty!==undefined)h+=`<option value="">${esc(empty)}</option>`;for(const o of L)h+=`<option value="${escA(o)}" ${String(o)===String(val)?'selected':''}>${dsp(esc(o))}</option>`;if(val!==undefined&&val!==null&&val!==''&&!L.includes(String(val)))h+=`<option value="${escA(val)}" selected>${dsp(esc(val))}</option>`;return h;}
function tab(t){
 if(t!==cur){viewEpoch++; cancelReads();}
 clearTimeout(searchTimer);
 cur=t;
 g('q').value=queries[t]||'';
 updateHeading(t);
 g('pager').innerHTML='';
 g('tablewrap').innerHTML='';
 g('extrawrap').innerHTML='';
 g('request-error').hidden=true;
 document.body.classList.remove('navigation-open');
 document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.t===t));
 document.getElementById('cardgen').style.display=(t==='cards')?'block':'none';
 if(t==='cards')ensureMeta().catch(reportError);
 const addBtn=document.getElementById('addBtn'); if(addBtn) addBtn.style.display=(t==='portal_accounts'||t==='custom_reviews'||t==='custom_pets'||t==='feedbacks'||t==='app_release'||t==='lottery'||t==='zhongyuan'||t==='push'||t==='celebrate'||t==='assistant_free'||t==='audit')?'none':'';
 const bar=document.querySelector('main>.bar'); if(bar) bar.style.display=(t==='app_release'||t==='lottery'||t==='zhongyuan'||t==='push'||t==='celebrate'||t==='assistant_free'||t==='audit')?'none':'';
 if(t==='portal_accounts') loadPortalAccounts();
 else if(t==='custom_reviews') loadCustomReviews();
 else if(t==='custom_pets') loadCustomPets();
 else if(t==='feedbacks') loadFeedbacks();
 else if(t==='app_release') loadAppRelease();
 else if(t==='lottery') ensureMeta().then(()=>{if(cur==='lottery')return loadLottery();}).catch(reportError);
 else if(t==='zhongyuan') loadZhongyuan();
 else if(t==='push') loadPush();
 else if(t==='celebrate') loadCelebrate();
 else if(t==='assistant_free') loadAssistantFree();
 else if(t==='audit') loadAudit();
 else load();
}
async function api(p,b){return requestJSON(p,b);}
async function load(){
 const special={portal_accounts:loadPortalAccounts,custom_reviews:loadCustomReviews,custom_pets:loadCustomPets,feedbacks:loadFeedbacks,app_release:loadAppRelease,lottery:loadLottery,zhongyuan:loadZhongyuan,push:loadPush,celebrate:loadCelebrate,assistant_free:loadAssistantFree,audit:loadAudit};
 if(special[cur]) return special[cur]();
 const table=cur;
 const r=await readPage('/api/list',{table},table);
 cache=r.data||{}; render();
}

function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function tj(k){return escA(JSON.stringify(k)).replace(/'/g,'&#39;');}
function fdate(ts){if(!ts)return '—';const d=new Date(ts*1000);return d.toLocaleString('zh-CN',{hour12:false});}
function match(){return true;} // Search is applied server-side before pagination.
function render(){
 if(cur==='players')renderPlayers();
 else if(cur==='groups')renderGroups();
 else if(cur==='events')renderEvents();
 else if(cur==='portal_accounts')renderPortalAccounts();
 else if(cur==='custom_reviews')renderCustomReviews();
 else if(cur==='custom_pets')renderCustomPets();
 else if(cur==='feedbacks')renderFeedbacks();
 else if(cur==='zhongyuan'){ /* 由 renderZhongyuan 自绘 */ }
 else if(cur==='push'){ /* 由 renderPush 自绘 */ }
 else if(cur==='celebrate'){ /* 由 renderCelebrate 自绘 */ }
 else if(cur==='assistant_free'){ /* 由 renderAssistantFree 自绘 */ }
 else if(cur==='audit'){ /* 由 renderAudit 自绘 */ }
 else renderCards();
}
// ---- 口令抽奖（管理表单；奖品从全部货币 + 全部道具中选择，全群共享）----
async function loadLottery(){
 const r=await api('/api/lottery/state',{});
 LOTTERY=r.data||null;
 renderLottery();
}
function renderLottery(){
 document.getElementById('count').textContent='';
 document.getElementById('extrawrap').innerHTML='';
 const l=LOTTERY||{};
 const prize=l.prize||{};
 const kind=prize.kind||'currency';
 const count=Number(prize.count)||1;
 const curName=(kind==='currency')?(prize.name||'灵石'):(prize.name||'');
 const winners=(l.winners||[]).map(esc).join('、')||'（未开奖）';
 const entriesN=l.entries?Object.keys(l.entries).length:0;
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:880px">
  <div style="background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px;padding:22px">
   <h3 style="margin:0 0 16px">口令抽奖 <span class="muted" style="font-weight:400">（玩家输入口令参与；到点自动开奖并全群播报）</span></h3>
   <div class="row">
    <label class="fld">启用 <input id="lt_enabled" type="checkbox" ${l.enabled?'checked':''}></label>
    <label class="fld">开奖方式 <select id="lt_mode"><option value="lottery" ${l.mode==='lottery'?'selected':''}>随机抽取</option><option value="claim" ${l.mode==='claim'?'selected':''}>先到先得</option></select></label>
   </div>
   <label class="fld">口令 <input id="lt_password" placeholder="如：一起发财" value="${esc(l.password||'')}"></label>
   <div class="row">
    <label class="fld">份数 / 中奖人数 <input id="lt_quantity" type="number" value="${Number(l.quantity)||10}" style="width:140px"></label>
    <label class="fld">开奖时间 <input id="lt_draw_at" type="datetime-local" value="${eventTsToLocal(l.draw_at)}"></label>
   </div>
   <div class="sec">奖品（全部货币 / 全部道具，二选一）</div>
   <div class="row">
    <label class="fld">类型 <select id="lt_kind" onchange="ltFillName()"><option value="currency" ${kind==='currency'?'selected':''}>货币</option><option value="item" ${kind==='item'?'selected':''}>道具</option></select></label>
    <label class="fld">名称 <select id="lt_name"></select></label>
    <label class="fld">数量 <input id="lt_count" type="number" value="${count}" style="width:110px"></label>
   </div>
   <div class="sec">全群播报文本</div>
   <div class="muted" style="margin:-4px 0 8px">支持占位符：<code>{{password}}</code> <code>{{prize}}</code> <code>{{count}}</code> <code>{{total}}</code> <code>{{mode}}</code> <code>{{winners}}</code>（留空用默认模板）</div>
   <textarea id="lt_broadcast" rows="4" placeholder="留空使用默认开奖公告" style="width:100%;padding:10px 12px;border:1px solid #d8d7c9;border-radius:3px;resize:vertical">${esc(l.broadcast_text||'')}</textarea>
   <label class="fld" style="margin-top:12px"><input id="lt_reset" type="checkbox"> 重置为全新抽奖（清空已有报名与结果）</label>
   <div style="margin-top:16px;display:flex;gap:10px">
    <button class="act" onclick="saveLottery()">保存</button>
    <button class="act del" onclick="lotteryDraw()">立即开奖</button>
    <button class="act ghost" onclick="loadLottery()">刷新</button>
   </div>
   <div class="muted" id="lt_msg" style="margin-top:10px"></div>
  </div>
  <div style="margin-top:12px;padding:14px;background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px">
   <span class="muted">报名人数：</span><b>${entriesN}</b>
   &nbsp;&nbsp;<span class="muted">状态：</span><b>${l.drawn?'已开奖':'进行中'}</b>
   &nbsp;&nbsp;<span class="muted">中奖名单：</span><span>${winners}</span>
  </div>
 </div>`;
 ltFillName();
}
function ltFillName(){
 const kind=(g('lt_kind').value||'currency');
 const list=kind==='currency'?(META.currencies||['金币','积分','钻石']):(META.items||[]);
 const cur=((LOTTERY||{}).prize||{}).name||'';
 g('lt_name').innerHTML=optHtml(list, cur, kind==='currency'?'': '道具名称');
}
async function saveLottery(){
 const kind=g('lt_kind').value;
 const cfg={
  enabled:g('lt_enabled').checked,
  mode:g('lt_mode').value,
  password:g('lt_password').value.trim(),
  quantity:Number(g('lt_quantity').value)||0,
  draw_at:eventLocalToTs(g('lt_draw_at').value)||0,
  prize:{kind, name:g('lt_name').value, count:Number(g('lt_count').value)||1},
  broadcast_text:g('lt_broadcast').value.trim(),
 };
 if(!cfg.password){alert('请填写口令');return;}
 if(!cfg.quantity){alert('请填写份数 / 中奖人数');return;}
 const r=await api('/api/lottery/save',{cfg, reset:g('lt_reset').checked});
 g('lt_msg').textContent=r.ok?' 已保存':' 保存失败：'+(r.msg||'');
 if(r.ok) loadLottery();
}
async function lotteryDraw(){
 if(!confirm('确认立即开奖？')) return;
 const r=await api('/api/lottery/draw',{});
 alert(r.ok?(r.msg||' 已开奖'):(r.msg||'开奖失败'));
 loadLottery();
}
// ---- 中元活动（独立模块配置，保存即时生效）----
const ZY_FIELDS=[
 {k:'enabled',label:'活动总开关',t:'bool'},
 {k:'start_at',label:'开始时间(0=不限)',t:'ts'},
 {k:'end_at',label:'结束时间(0=不限)',t:'ts'},
 {k:'open_hour',label:'每日开放小时(含)',t:'num'},
 {k:'close_hour',label:'每日关闭小时(不含)',t:'num'},
 {k:'trigger_interval_min',label:'解密触发间隔(分)',t:'num'},
 {k:'dungeon_limit_min',label:'单场解密时限(分)',t:'num'},
 {k:'bind_open_hours_before',label:'绑定提前开启(时)',t:'num'},
 {k:'bind_close_hours_before',label:'绑定截止(时)',t:'num'},
 {k:'redeem_window_hours',label:'兑换窗口(时)',t:'num'},
 {k:'max_draw_per_day',label:'每人每日被抽上限(0=不限)',t:'num'},
 {k:'max_dungeon_per_day',label:'副本每日开本上限(0=不限)',t:'num'},
 {k:'puzzle_count',label:'每场题数(协作)',t:'num'},
 {k:'answer_cooldown_sec',label:'答对后冷却(秒)',t:'num'},
 {k:'individual_fail_wrong',label:'个人答错出局次数',t:'num'},
 {k:'pull_min_pct',label:'拉入人数下限%',t:'num'},
 {k:'pull_max_pct',label:'拉入人数上限%',t:'num'},
 {k:'gongde_clear',label:'通关基础功德',t:'num'},
 {k:'perfect_reward_mult',label:'完美奖励倍数',t:'num'},
 {k:'lantern_daily_limit',label:'放河灯每日次数',t:'num'},
 {k:'incense_daily_limit',label:'供灯/焚香每日次数',t:'num'},
 {k:'quiz_daily_limit',label:'问答每日次数',t:'num'},
 {k:'lantern_cooldown_min',label:'放河灯冷却(分)',t:'num'},
 {k:'incense_cooldown_min',label:'供灯/焚香冷却(分)',t:'num'},
 {k:'quiz_timeout_sec',label:'问答超时(秒)',t:'num'},
 {k:'gongde_lantern_min',label:'放河灯功德下限',t:'num'},
 {k:'gongde_lantern_max',label:'放河灯功德上限',t:'num'},
 {k:'gongde_incense_min',label:'供灯/焚香功德下限',t:'num'},
 {k:'gongde_incense_max',label:'供灯/焚香功德上限',t:'num'},
 {k:'gongde_quiz_min',label:'问答功德下限',t:'num'},
 {k:'gongde_quiz_max',label:'问答功德上限',t:'num'},
 {k:'gongde_sign',label:'签到功德',t:'num'},
 {k:'yin_penalty_min',label:'阴气缠身时长(分)',t:'num'},
 {k:'yin_clear_cost',label:'解除阴气消耗',t:'num'},
 {k:'deepseek_enabled',label:'启用 DeepSeek',t:'bool'},
 {k:'deepseek_model',label:'DeepSeek 模型',t:'txt'},
 {k:'deepseek_base_url',label:'接口地址',t:'txt'},
 {k:'deepseek_api_key',label:'API Key',t:'pwd'},
 {k:'deepseek_temperature',label:'温度',t:'num',step:'0.1'},
 {k:'deepseek_max_tokens',label:'最大 tokens',t:'num'},
 {k:'deepseek_timeout',label:'超时(秒)',t:'num'},
];
let ZY_CFG=null;
async function loadZhongyuan(){
 const r=await api('/api/zhongyuan/config',{});
 ZY_CFG=(r&&r.ok)?r.data:null;
 renderZhongyuan();
}
function renderZhongyuan(){
 document.getElementById('count').textContent='';
 document.getElementById('extrawrap').innerHTML='';
 const c=ZY_CFG||{};
 let rows='';
 for(const f of ZY_FIELDS){
  const v=c[f.k];
  let inp;
  if(f.t==='bool') inp=`<input id="zy_${f.k}" type="checkbox" ${v?'checked':''}>`;
  else if(f.t==='ts') inp=`<input id="zy_${f.k}" type="datetime-local" value="${eventTsToLocal(v||0)}">`;
  else if(f.t==='pwd') inp=`<input id="zy_${f.k}" type="password" autocomplete="off" placeholder="${v?'已设置（留空不修改）':'未设置'}" value="">`;
  else if(f.t==='num') inp=`<input id="zy_${f.k}" type="number" step="${f.step||'1'}" value="${(v===undefined||v===null)?'':v}">`;
  else inp=`<input id="zy_${f.k}" value="${esc(v==null?'':v)}">`;
  rows+=`<label class="fld">${f.label} ${inp}</label>`;
 }
 const tiers=JSON.stringify(c.tiers||[],null,2);
 const miles=JSON.stringify(c.milestones||[],null,2);
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:960px">
  <div style="background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px;padding:22px">
   <h3 style="margin:0 0 16px"> 中元节活动 <span class="muted" style="font-weight:400">（独立模块配置，保存即时生效）</span></h3>
   <div class="sec">总控 / 时间 / 抽人 / 解密 / 功德</div>
   <div class="row">${rows}</div>
   <div class="sec">段位（前 20 名功德奖励，JSON：name / min / max / gongde）</div>
   <textarea id="zy_tiers" rows="5" style="width:100%;padding:10px 12px;border:1px solid #d8d7c9;border-radius:3px;resize:vertical;font-family:monospace">${esc(tiers)}</textarea>
   <div class="sec">群里程碑（累计功德达标，JSON：threshold / gongde）</div>
   <textarea id="zy_milestones" rows="5" style="width:100%;padding:10px 12px;border:1px solid #d8d7c9;border-radius:3px;resize:vertical;font-family:monospace">${esc(miles)}</textarea>
   <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
    <button class="act" onclick="saveZhongyuan()">保存配置</button>
    <button class="act ghost" onclick="loadZhongyuan()">刷新</button>
    <button class="act ghost" onclick="testDeepSeek()">测试 DeepSeek 连接</button>
    <button class="act ghost" onclick="testZhongyuanBroadcast()">全群通报测试</button>
    <button class="act ghost" onclick="testZhongyuanStart()">测试活动开始全群播放</button>
    <button class="act ghost" onclick="testZhongyuanEnd()">测试活动结束全群播放</button>
    <button class="act ghost" onclick="viewZhongyuanData()">查看中元所有数据</button>
    <button class="act del" onclick="clearZhongyuanData()">清空中元玩家数据</button>
   </div>
   <div class="muted" id="zy_msg" style="margin-top:10px"></div>
   <div class="muted" id="zy_test_msg" style="margin-top:6px"></div>
   <div class="muted" id="zy_data_msg" style="margin-top:6px"></div>
   <textarea id="zy_data_box" rows="16" style="display:none;width:100%;margin-top:8px;padding:10px 12px;border:1px solid #d8d7c9;border-radius:3px;font-family:monospace;font-size:12px;white-space:pre" readonly></textarea>
  </div>
 </div>`;
}
async function saveZhongyuan(){
 const cfg={};
 for(const f of ZY_FIELDS){
  const el=g('zy_'+f.k);
  if(f.t==='bool') cfg[f.k]=el.checked;
  else if(f.t==='pwd'){const v=el.value.trim(); if(v!=='') cfg[f.k]=v;} // 留空 = 不修改
  else if(f.t==='ts') cfg[f.k]=eventLocalToTs(el.value)||0;
  else if(f.t==='num'){const raw=el.value;cfg[f.k]=(raw===''?(ZY_CFG&&ZY_CFG[f.k]!==undefined?ZY_CFG[f.k]:0):parseFloat(raw));}
  else cfg[f.k]=el.value.trim();
 }
 try{cfg.tiers=JSON.parse(g('zy_tiers').value);}catch(e){alert('段位 JSON 解析失败：'+e.message);return;}
 try{cfg.milestones=JSON.parse(g('zy_milestones').value);}catch(e){alert('里程碑 JSON 解析失败：'+e.message);return;}
 const r=await api('/api/zhongyuan/config/save',{config:cfg});
 const msg=g('zy_msg');
 if(!r){msg.textContent=' 保存失败：无响应';return;}
 msg.textContent=r.ok?(' 已保存'+(r.bad&&r.bad.length?'（跳过：'+r.bad.join(', ')+'）':'')):(' 保存失败：'+(r.msg||'未知错误'));
 if(r.ok) loadZhongyuan();
}
// ---- 自定义文本群推送（手动 / 定时一次性 / 定时循环）----
let PUSH_DATA=null;
async function loadPush(){
 const r=await api('/api/push/state',{});
 PUSH_DATA=(r&&r.ok)?r.data:{jobs:[]};
 renderPush();
}
function pushModeChange(){
 const el=g('push_mode'); const m=el?el.value:'once';
 const once=g('push_once_row'), rec=g('push_recur_row');
 if(once) once.style.display=(m==='once')?'':'none';
 if(rec) rec.style.display=(m==='recurring')?'':'none';
}
function renderPush(){
 document.getElementById('count').textContent='';
 document.getElementById('extrawrap').innerHTML='';
 const jobs=(PUSH_DATA&&PUSH_DATA.jobs)||[];
 let rows='';
 for(const j of jobs){
  const lr=j.last_result||{};
  let lrs='（未推送）';
  if(lr.ts){
   lrs=`${fdate(lr.ts)} · 目标${lr.targets??'—'}/成功${lr.sent??'—'}/失败${lr.failed??'—'}`+(lr.error?(' · '+esc(lr.error)):'');
  }
  let when='';
  if(j.mode==='once') when=j.done?('已完成 · '+fdate(j.target_ts)):('到点 '+fdate(j.target_ts));
  else when=('下次 '+fdate(j.next_run)+' · 每'+j.interval_min+'分钟');
  const stCol=j.enabled?'<b style="color:#1e6b45">启用</b>':'<span class="muted">停用</span>';
  const modeCol=j.mode==='once'?'一次性':'循环';
  const statusBtn=j.enabled?'停用':'启用';
  rows+=`<tr>
   <td class="muted">${esc(j.id.slice(-8))}</td>
   <td>${modeCol}</td>
   <td style="max-width:200px;overflow-wrap:anywhere">${esc(j.text)}</td>
   <td>${when}</td>
   <td class="muted" style="font-size:12px">${lrs}</td>
   <td>${stCol}</td>
   <td>
    <button class="act ghost" onclick="pushToggle('${escA(j.id)}',${!j.enabled})">${statusBtn}</button>
    <button class="act ghost" onclick="pushFire('${escA(j.id)}')">触发</button>
    <button class="act del" onclick="pushDelete('${escA(j.id)}')">删除</button>
   </td></tr>`;
 }
 const tableHtml=rows?`<table><thead><tr><th>ID</th><th>模式</th><th>文案</th><th>排程</th><th>最近结果</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>`:'<div class="empty">暂无定时任务</div>';
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:960px">
  <div style="background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px;padding:22px">
   <h3 style="margin:0 0 16px"> 自定义文本群推送 <span class="muted" style="font-weight:400">（推送到所有已授权且开启灵契仙途玩法的群）</span></h3>
   <div class="row">
    <label class="fld">模式 <select id="push_mode" onchange="pushModeChange()">
      <option value="once">指定时间发送（一次性）</option>
      <option value="recurring">定时循环（每隔 N 分钟）</option>
    </select></label>
    <label class="fld">任务名称 <input id="push_name" placeholder="可选" style="width:180px"></label>
   </div>
   <label class="fld">推送文案 <textarea id="push_text" rows="4" placeholder="推送给所有群的内容，支持 Markdown：用空行/列表分段，勿用单个换行" style="width:100%;padding:10px 12px;border:1px solid #d8d7c9;border-radius:3px;resize:vertical"></textarea></label>
   <div class="row">
    <label class="fld" id="push_once_row">指定时间 <input id="push_at" type="datetime-local"></label>
    <label class="fld" id="push_recur_row" style="display:none">间隔(分钟) <input id="push_interval" type="number" value="30" style="width:100px"></label>
   </div>
   <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
    <button class="act" onclick="pushManual()">立即推送到所有授权群</button>
    <button class="act" onclick="pushSave()">新建定时任务</button>
    <button class="act ghost" onclick="loadPush()">刷新</button>
   </div>
   <div class="muted" id="push_msg" style="margin-top:10px"></div>
  </div>
  <div style="margin-top:12px;background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px;padding:10px 14px">
   <div class="sec">定时任务列表</div>
   ${tableHtml}
  </div>
 </div>`;
 pushModeChange();
 paginateFormTables();
}
async function pushManual(){
 const text=g('push_text').value.trim();
 if(!text){alert('请填写推送文案'); return;}
 const r=await api('/api/push/manual',{text});
 const m=g('push_msg'); if(m) m.textContent=(r.ok?' ':' ')+(r.msg||'');
}
async function pushSave(){
 const mode=g('push_mode').value;
 const text=g('push_text').value.trim();
 const name=g('push_name').value.trim();
 if(!text){alert('请填写推送文案'); return;}
 const body={mode,text,name};
 if(mode==='once'){
  body.target_ts=eventLocalToTs(g('push_at').value)||0;
  if(!body.target_ts){alert('请选择指定时间'); return;}
 } else {
  body.interval_min=Number(g('push_interval').value)||0;
  if(!body.interval_min){alert('请填写间隔分钟'); return;}
 }
 const r=await api('/api/push/save',body);
 const m=g('push_msg'); if(m) m.textContent=(r.ok?' ':' ')+(r.msg||'');
 if(r.ok) loadPush();
}
async function pushToggle(id,en){
 const r=await api('/api/push/toggle',{id,enabled:en});
 const m=g('push_msg'); if(m) m.textContent=(r.ok?' ':' ')+(r.msg||'');
 if(r.ok) loadPush();
}
async function pushFire(id){
 if(!confirm('确认立即触发推送到所有授权群？')) return;
 const r=await api('/api/push/fire',{id});
 alert(r.ok?(r.msg||'已触发'):(r.msg||'触发失败'));
 if(r.ok) loadPush();
}
async function pushDelete(id){
 if(!confirm('确认删除该定时任务？')) return;
 const r=await api('/api/push/delete',{id});
 const m=g('push_msg'); if(m) m.textContent=(r.ok?' ':' ')+(r.msg||'');
 if(r.ok) loadPush();
}
// --------------------------- 生辰盛典（每日定时开奖箱 + 奖池瓜分）后台 ---------------------------
let CELEBRATE=null;
async function loadCelebrate(){
 const r=await api('/api/celebrate/state',{});
 CELEBRATE=(r&&r.ok)?r.data:{};
 if(!CELEBRATE.gacha) CELEBRATE.gacha={cmd:'生日抽奖',menu_cmd:'生辰活动',rounds:[]};
 if(!CELEBRATE.pool) CELEBRATE.pool={cmd:'生日快乐',start_time:'07:00',cooldown_min:15,cooldown_max:30,currencies:{}};
 // 动态库存：以数组便于增删行
 const st=CELEBRATE.gacha.stock||{};
 CELEBRATE._stock=Object.keys(st).map(n=>({name:n,count:st[n]}));
 renderCelebrate();
}
function ceRoundDrawAt(timeStr){
 if(!timeStr) return 0;
 const s=Number((CELEBRATE&&CELEBRATE.start_at)||0);
 const base=s?new Date(s*1000):new Date();
 const p=String(timeStr).split(':');
 base.setHours(Number(p[0])||0, Number(p[1])||0, 0, 0);
 return Math.floor(base.getTime()/1000);
}
function ceRoundRow(r,i){
 r=r||{};
 const drawn=r.drawn?`<span style="color:#1e6b45">已开奖</span>`:'<span class="muted">未开奖</span>';
 return `<tr>
  <td class="muted">${drawn}</td>
  <td><input type="time" value="${esc(r.time||'')}" id="ce_rt${i}" style="width:105px"></td>
  <td><button class="act del" onclick="ceDelRound(${i})">删</button></td>
 </tr>`;
}
function ceStockRow(s,i){
 s=s||{};
 return `<tr>
  <td><input value="${esc(s.name||'')}" id="ce_sn${i}" placeholder="奖品名（如 变种卡）" style="width:150px"></td>
  <td><input type="number" min="0" value="${s.count||0}" id="ce_sc${i}" style="width:100px"></td>
  <td><button class="act del" onclick="ceDelStock(${i})">删</button></td>
 </tr>`;
}
function renderCelebrate(){
 document.getElementById('count').textContent='';
 document.getElementById('extrawrap').innerHTML='';
 const c=CELEBRATE||{};
 const ga=c.gacha||{}, po=c.pool||{};
 const rounds=ga.rounds||[];
 let rrows='';
 for(let i=0;i<rounds.length;i++) rrows+=ceRoundRow(rounds[i],i);
 const cur=po.currencies||{};
 let curRows='';
 for(const nm of ['金币','积分','钻石']){
  const cc=cur[nm]||{};
  curRows+=`<tr>
   <td><input value="${dsp(nm)}" style="width:80px" readonly></td>
   <td><input type="number" min="0" value="${cc.total||0}" id="ce_ct_${nm}" style="width:140px"></td></tr>`;
 }
 const st=c.start_at||0, en=c.end_at||0;
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:1000px">
  <div style="background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px;padding:22px">
   <h3 style="margin:0 0 16px"> ${esc(c.name||'生辰盛典')} 后台配置 <span class="muted" style="font-weight:400">（每日多次定时开奖箱 + 奖池瓜分）</span></h3>
   <div class="row">
    <label class="fld">启用 <input type="checkbox" id="ce_on" ${c.enabled?'checked':''}></label>
    <label class="fld">名称 <input id="ce_name" value="${esc(c.name||'生辰盛典')}" style="width:150px"></label>
   </div>
   <div class="row">
    <label class="fld">开始 <input id="ce_start" type="datetime-local" value="${eventTsToLocal(st)}"></label>
    <label class="fld">结束 <input id="ce_end" type="datetime-local" value="${eventTsToLocal(en)}"></label>
   </div>
   <label class="fld">开启公告 <textarea id="ce_ann" rows="2" style="width:100%;padding:10px 12px;border:1px solid #d8d7c9;border-radius:3px">${esc(c.announce||'')}</textarea></label>
   <label class="fld" style="margin-top:10px">结束公告 <textarea id="ce_ann_end" rows="2" style="width:100%;padding:10px 12px;border:1px solid #d8d7c9;border-radius:3px">${esc(c.announce_end||'')}</textarea></label>
   <div class="sec" style="margin-top:16px"> 每小时推送「如何参与」（盛典窗口内循环提醒）</div>
   <div class="row">
    <label class="fld" style="width:auto">间隔(小时) <input type="number" min="0" value="${c.howto_interval_h||0}" id="ce_how_ih" style="width:90px"></label>
    <label class="fld" style="flex:1;min-width:280px">文案 <textarea id="ce_how" rows="3" style="width:100%;padding:10px 12px;border:1px solid #d8d7c9;border-radius:3px">${esc(c.howto||'')}</textarea></label>
   </div>
   <div style="color:#6b766c;font-size:12px">填 1 表示开奖期间每 1 小时全群推一次此文案；填 0 关闭。</div>
   <div class="sec" style="margin-top:18px"> 抽奖开奖箱（每轮按中奖率抽人，奖品从库存动态抽取）</div>
   <div class="row">
    <label class="fld">指令 <input id="ce_gcmd" value="${esc(ga.cmd||'生日抽奖')}" style="width:150px"></label>
    <label class="fld">菜单 <input id="ce_gmenu" value="${esc(ga.menu_cmd||'生辰活动')}" style="width:150px"></label>
    <label class="fld" style="width:auto">中奖率% <input type="number" min="1" max="100" value="${Math.round((ga.win_rate||0.8)*100)}" id="ce_gwr" style="width:80px"></label>
    <button class="act ghost" onclick="ceAddRound()">+ 加一场（共 ${rounds.length} 场）</button>
   </div>
   <div class="row">
    <label class="fld">压轴大奖 <input id="ce_ggitem" value="${esc(ga.grand_item||'宠物定制卡')}" style="width:150px"></label>
    <label class="fld">数量 <input type="number" min="1" value="${ga.grand_count||1}" id="ce_ggcnt" style="width:80px"></label>
    <span class="muted">仅最后一轮保发（${ga.grand_used?'已发放':'未发放'}）</span>
   </div>
   ${rounds.length?`<table><thead><tr><th>状态</th><th>时间</th><th></th></tr></thead><tbody>${rrows}</tbody></table>`:'<div class="empty">尚未配置开奖场次，点击上方「加一场」。</div>'}
   <div style="color:#6b766c;font-size:12px;margin-top:6px">时间填 HH:MM，开奖日期取「开始」时间的当天；每轮中奖人数≈参与人数×中奖率（上限=剩余库存）。</div>
   <div class="sec" style="margin-top:16px"> 动态库存（中奖者按剩余份数加权随机抽 1 份，抽完即止）</div>
   <table><thead><tr><th>奖品名</th><th>总量</th><th></th></tr></thead><tbody>${(CELEBRATE._stock||[]).map((s,i)=>ceStockRow(s,i)).join('')||'<tr><td colspan="3" class="muted">未配置库存</td></tr>'}</tbody></table>
   <div class="row" style="margin-top:6px">
    <button class="act ghost" onclick="ceAddStock()">+ 加一种</button>
    <button class="act ghost" onclick="resetStock()">重置库存剩余</button>
    <span class="muted" style="font-size:12px">「保存」后生效；重置将恢复剩余=总量并重置压轴大奖。</span>
   </div>
   <div class="sec" style="margin-top:20px"> 奖池瓜分（${esc(po.cmd||'生辰瓜分')}）</div>
   <div class="row">
    <label class="fld">启用瓜分 <input type="checkbox" id="ce_pon" ${po.enabled===false?'':'checked'}></label>
    <label class="fld">指令 <input id="ce_pcmd" value="${esc(po.cmd||'生辰瓜分')}" style="width:150px"></label>
    <label class="fld">每日开启 <input type="time" value="${po.start_time||'07:00'}" id="ce_pst" style="width:110px"></label>
   </div>
   <div class="row">
    <label class="fld">冷却最小值(分) <input type="number" min="1" value="${po.cooldown_min||15}" id="ce_pcdmin" style="width:90px"></label>
    <label class="fld">冷却最大值(分) <input type="number" min="1" value="${po.cooldown_max||30}" id="ce_pcdmax" style="width:90px"></label>
    <span class="muted" style="font-size:12px">每次瓜分后冷却时长在区间内随机（仅未勾选无冷却时生效）。</span>
   </div>
   <div class="row">
    <label class="fld">每次瓜分额度 <input type="number" min="0" value="${po.per_grab||1000}" id="ce_pgrab" style="width:100px"></label>
    <label class="fld">无冷却 <input type="checkbox" id="ce_pnocd" ${po.no_cd===false?'':'checked'}></label>
    <span class="muted" style="font-size:12px">玄晶/灵石各等额拿该值（上限=该币剩余）；天晶默认 100；勾选则关闭冷却、可连续点击。</span>
   </div>
   <table><thead><tr><th>货币</th><th>总量（动态库存）</th></tr></thead><tbody>${curRows||'<tr><td colspan="2" class="muted">未配置</td></tr>'}</tbody></table>
   <div style="color:#6b766c;font-size:12px">每日到达开启时间后开放瓜分；每次按等额固定额度抽取（玄晶/灵石各取 per_grab，天晶默认 100），而非随机比例；勾选无冷却可连续瓜分。</div>
   <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px">
    <button class="act" onclick="saveCelebrate()">保存配置</button>
    <button class="act ghost" onclick="resetPool()">重置奖池剩余</button>
    <button class="act ghost" onclick="broadcastAnnounce('start')">广播开启公告</button>
    <button class="act ghost" onclick="broadcastAnnounce('end')">广播结束公告</button>
    <button class="act ghost" onclick="loadCelebrate()">刷新</button>
   </div>
   <div class="muted" id="ce_msg" style="margin-top:10px"></div>
  </div>
 </div>`;
 paginateFormTables();
}
function ceAddRound(){ const c=CELEBRATE||{}; c.gacha=c.gacha||{cmd:'生日抽奖',menu_cmd:'生辰活动',rounds:[]}; c.gacha.rounds.push({time:'',draw_at:0}); renderCelebrate(); }
function ceDelRound(i){ const c=CELEBRATE||{}; if(c.gacha&&c.gacha.rounds){ c.gacha.rounds.splice(i,1); renderCelebrate(); } }
function ceAddStock(){ const c=CELEBRATE||{}; c._stock=c._stock||[]; c._stock.push({name:'',count:0}); renderCelebrate(); }
function ceDelStock(i){ const c=CELEBRATE||{}; if(c._stock){ c._stock.splice(i,1); renderCelebrate(); } }
async function resetStock(){
 if(!confirm('确认将「动态库存」剩余重置回各总量，并重置压轴大奖为未发放？')) return;
 const r=await api('/api/celebrate/reset_stock',{});
 const m=g('ce_msg'); if(m) m.textContent=(r.ok?' ':' ')+(r.msg||'');
}
async function saveCelebrate(){
 const c=CELEBRATE||{};
 const body={celebrate:{
  enabled: g('ce_on')?g('ce_on').checked:false,
  name: g('ce_name').value,
  start_at: eventLocalToTs(g('ce_start').value)||0,
  end_at: eventLocalToTs(g('ce_end').value)||0,
  announce: g('ce_ann').value,
  announce_end: g('ce_ann_end').value,
  howto: g('ce_how').value,
  howto_interval_h: Number(g('ce_how_ih').value)||0,
  gacha:{enabled:true, cmd:g('ce_gcmd').value||'生日抽奖', menu_cmd:g('ce_gmenu').value||'生辰活动',
         win_rate: Math.min(1,Math.max(0.01,(Number(g('ce_gwr').value)||80)/100)),
         grand_item:g('ce_ggitem').value||'宠物定制卡', grand_count:Math.max(1,Number(g('ce_ggcnt').value)||1),
         grand_used: !!((c.gacha||{}).grand_used),
         stock:{}, stock_remain:{}, rounds:[]},
  pool:{enabled:g('ce_pon')?g('ce_pon').checked:true, cmd:g('ce_pcmd').value||'生日快乐',
        start_time:g('ce_pst').value||'07:00',
        cooldown_min:Math.max(1,Number(g('ce_pcdmin').value)||15),
        cooldown_max:Math.max(1,Number(g('ce_pcdmax').value)||30),
        per_grab:Math.max(0,Number(g('ce_pgrab').value)||1000),
        no_cd:g('ce_pnocd')?g('ce_pnocd').checked:true,
        currencies:{}}
 }};
 const nRounds=((c.gacha||{}).rounds||[]).length;
 for(let i=0;i<nRounds;i++){
  const tEl=document.getElementById('ce_rt'+i); if(!tEl) continue;
  const time=tEl.value;
  body.celebrate.gacha.rounds.push({time, draw_at: ceRoundDrawAt(time)});
 }
 for(const s of (c._stock||[])){
  const nm=(s.name||'').trim(); if(!nm) continue;
  body.celebrate.gacha.stock[nm]=Math.max(0,Number(s.count)||0);
 }
 const oldRemain=((c.gacha||{}).stock_remain)||{};
 for(const nm in body.celebrate.gacha.stock){
  if(typeof oldRemain[nm]==='number') body.celebrate.gacha.stock_remain[nm]=Math.min(oldRemain[nm],body.celebrate.gacha.stock[nm]);
  else body.celebrate.gacha.stock_remain[nm]=body.celebrate.gacha.stock[nm];
 }
 for(const nm of ['积分','金币','钻石']){
  const tEl=document.getElementById('ce_ct_'+nm); if(!tEl) continue;
  body.celebrate.pool.currencies[nm]={total:Number(tEl.value)||0};
 }
 const r=await api('/api/celebrate/save',body);
 const m=g('ce_msg'); if(m) m.textContent=(r.ok?' ':' ')+(r.msg||'');
 if(r.ok) loadCelebrate();
}
async function resetPool(){
 if(!confirm('确认将奖池剩余重置回配置总额？')) return;
 const r=await api('/api/celebrate/reset_pool',{});
 const m=g('ce_msg'); if(m) m.textContent=(r.ok?' ':' ')+(r.msg||'');
}
async function broadcastAnnounce(which){
 if(!confirm(which==='start'?'确认向所有已授权群真实广播「开启公告」？':'确认向所有已授权群真实广播「结束公告」？')) return;
 const r=await api('/api/celebrate/broadcast',{which});
 alert(r.ok?(r.msg||'已广播'):(r.msg||'广播失败'));
}
// ---------------- 限时免费使用自动助手（全局一次性时间窗口） ----------------
let AFREE=null;
async function loadAssistantFree(){
 const r=await api('/api/assistant_free/state',{});
 AFREE=(r&&r.ok)?r.data:{enabled:false,start_at:0,end_at:0};
 AFREE._active=!!(r&&r.active);
 AFREE._now=(r&&r.now)||Math.floor(Date.now()/1000);
 renderAssistantFree();
}
function renderAssistantFree(){
 const cnt=document.getElementById('count'); if(cnt) cnt.textContent='';
 const ex=document.getElementById('extrawrap'); if(ex) ex.innerHTML='';
 const c=AFREE||{};
 let stateTxt=' 未在窗口内';
 if(!c.enabled) stateTxt=' 未启用';
 else if(c._active) stateTxt=' 免费窗口生效中：执行任务不消耗次数，剩余 0 次也能正常运行';
 else if(Number(c.start_at||0)>Number(c._now||0)) stateTxt='⏳ 未开始（还没到开始时间）';
 else stateTxt=' 已结束（结束时间已过）';
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:820px">
  <div style="background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px;padding:22px">
   <h3 style="margin:0 0 6px"> 限时免费使用自动助手 <span class="muted" style="font-weight:400">（全局生效）</span></h3>
   <div style="color:#6b766c;font-size:12px;margin-bottom:16px">在设置的时间范围内，全服玩家执行自动助手任务均不消耗次数，剩余次数为 0 的玩家也能正常开启与使用。窗口结束后自动恢复原有计费与停机规则，无需人工干预。</div>
   <div class="row">
    <label class="fld">启用 <input type="checkbox" id="af_on" ${c.enabled?'checked':''}></label>
   </div>
   <div class="row">
    <label class="fld">开始 <input id="af_start" type="datetime-local" value="${eventTsToLocal(c.start_at)}"></label>
    <label class="fld">结束 <input id="af_end" type="datetime-local" value="${eventTsToLocal(c.end_at)}"></label>
   </div>
   <div style="margin-top:12px;font-size:13px">当前状态：<b>${stateTxt}</b></div>
   <div style="color:#6b766c;font-size:12px;margin-top:6px">起止均为本机时区时间；结束时间早于开始时间则该窗口不会生效。</div>
   <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px">
    <button class="act" onclick="saveAssistantFree()">保存配置</button>
    <button class="act ghost" onclick="loadAssistantFree()">刷新</button>
   </div>
   <div class="muted" id="af_msg" style="margin-top:10px"></div>
  </div>
 </div>`;
}
async function saveAssistantFree(){
 const body={assistant_free:{
  enabled: g('af_on')?g('af_on').checked:false,
  start_at: eventLocalToTs(g('af_start').value)||0,
  end_at: eventLocalToTs(g('af_end').value)||0
 }};
 const r=await api('/api/assistant_free/save',body);
 const m=g('af_msg'); if(m) m.textContent=(r.ok?' ':' ')+(r.msg||'');
 if(r.ok) loadAssistantFree();
}
// ---------------- 数据追溯（append-only 审计流水 + 规则/基线异常检测） ----------------
let AUDIT={log:[],total:0,page:1,size:10,flags:[],loading:false};
async function loadAudit(){
 renderAudit();
 auditQuery(true);
 auditFlags();
}
function renderAudit(){
 const cnt=document.getElementById('count'); if(cnt) cnt.textContent='';
 const ex=document.getElementById('extrawrap'); if(ex) ex.innerHTML='';
 document.getElementById('tablewrap').innerHTML=`
 <div style="max-width:1100px">
  <div style="background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px;padding:20px;margin-bottom:16px">
   <h3 style="margin:0 0 4px"> 审计流水查询</h3>
   <div style="color:#6b766c;font-size:12px;margin-bottom:12px">append-only 操作流水，每条带 SHA-256 哈希链锚定、防篡改可校验。支持按动作/群/用户/时间范围/关键字筛选。</div>
   <div class="row" style="flex-wrap:wrap">
    <label class="fld">动作
     <select id="au_action">
      <option value="">全部</option>
      <option value="cmd">cmd 指令</option>
      <option value="transfer">transfer 转让</option>
      <option value="buy">buy 购买</option>
      <option value="sign">sign 签到</option>
      <option value="redeem">redeem 兑换</option>
      <option value="get_pet">get_pet 宠物</option>
      <option value="admin_coin">admin_coin 管理员币</option>
      <option value="admin_item">admin_item 管理员道具</option>
      <option value="admin_bonus">admin_bonus 加次数</option>
      <option value="ban">ban 封号</option>
      <option value="unban">unban 解封</option>
      <option value="lottery">lottery 抽奖</option>
     </select>
    </label>
    <label class="fld">群 <input id="au_group" placeholder="群号" style="width:110px"></label>
    <label class="fld">用户 <input id="au_pid" placeholder="用户ID" style="width:120px"></label>
    <label class="fld">从 <input id="au_from" type="datetime-local"></label>
    <label class="fld">到 <input id="au_to" type="datetime-local"></label>
    <label class="fld">关键字 <input id="au_kw" placeholder="详情/QQ" style="width:120px"></label>
   </div>
   <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px">
    <button class="act" onclick="auditQuery(true)">查询</button>
    <button class="act ghost" onclick="auditVerify()">校验哈希链</button>
    <button class="act ghost" onclick="auditScan()">全量异常扫描</button>
   </div>
   <div id="au_msg" class="muted" style="margin-top:8px"></div>
  </div>
  <div id="au_log"></div>
  <div style="background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px;padding:20px;margin-top:16px">
   <h3 style="margin:0 0 4px"> 异常标记 <span class="muted" style="font-weight:400" id="au_flag_sum"></span></h3>
   <div style="color:#6b766c;font-size:12px;margin-bottom:12px">规则/基线检测命中后落表，可人工标记「已处理/误报」。同规则同目标只保留一条待处理，避免刷屏。</div>
   <div class="row">
    <label class="fld">状态
     <select id="au_flag_status" onchange="auditFlags()">
      <option value="">全部</option>
      <option value="open">待处理</option>
      <option value="handled">已处理</option>
      <option value="false_positive">误报</option>
     </select>
    </label>
   </div>
   <div id="au_flags" style="margin-top:8px"></div>
  </div>
 </div>`;
}
async function auditQuery(reset){
 if(reset) AUDIT.page=1;
 AUDIT.size=10;
 const msg=g('au_msg'); if(msg) msg.textContent='⏳ 查询中…';
 const body={
  action: g('au_action')?g('au_action').value:'',
  group: g('au_group')?g('au_group').value.trim():'',
  pid: g('au_pid')?g('au_pid').value.trim():'',
  ts_from: g('au_from')?eventLocalToTs(g('au_from').value)||0:0,
  ts_to: g('au_to')?eventLocalToTs(g('au_to').value)||0:0,
  kw: g('au_kw')?g('au_kw').value.trim():'',
  page: AUDIT.page, size: AUDIT.size
 };
 const r=await api('/api/audit/query',body);
 if(!r||!r.ok){ if(msg) msg.textContent=' 查询失败'; return; }
 AUDIT.log=r.items||[]; AUDIT.total=r.total||0; AUDIT.page=r.page||1;
 renderAuditLog();
 if(msg) msg.textContent=` 共 ${AUDIT.total} 条`;
}
function renderAuditLog(){
 const box=g('au_log'); if(!box) return;
 const log=AUDIT.log||[];
 const total=AUDIT.total, page=AUDIT.page, size=AUDIT.size;
 const pages=Math.max(1,Math.ceil(total/size));
 let h='<div style="background:#faf8f1;border:1px solid #d8d7c9;border-radius:6px;padding:16px">';
 h+=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
   <b>流水记录</b>
   <span class="muted">第 ${page}/${pages} 页 · 共 ${total} 条</span>
  </div>`;
 if(!log.length){ h+='<div class="empty">无匹配流水</div>'; }
 else{
  h+='<table style="width:100%;border-collapse:collapse;font-size:12px">';
  h+='<tr style="text-align:left;color:#6b766c"><th style="padding:4px 6px">时间</th><th style="padding:4px 6px">群</th><th style="padding:4px 6px">用户</th><th style="padding:4px 6px">动作</th><th style="padding:4px 6px">详情</th><th style="padding:4px 6px">变动</th></tr>';
  for(const rec of log){
   const del=rec.delta;
   let delTxt='—';
   if(typeof del==='number') delTxt=(del>=0?'+':'')+del;
   h+=`<tr style="border-top:1px solid #e7e4d5">
    <td style="padding:5px 6px;white-space:nowrap">${fdate(rec.ts)}</td>
    <td style="padding:5px 6px">${esc(rec.group||'')}</td>
    <td style="padding:5px 6px">${esc(rec.pid||'')}</td>
    <td style="padding:5px 6px"><span class="tag">${esc(rec.action||'')}</span></td>
    <td style="padding:5px 6px;max-width:340px;word-break:break-all">${esc(rec.detail||'')}</td>
    <td style="padding:5px 6px;white-space:nowrap">${delTxt}</td>
   </tr>`;
  }
  h+='</table>';
  h+=`<div style="display:flex;gap:10px;margin-top:12px">
   <button class="act ghost" ${page<=1?'disabled':''} onclick="auditPage(-1)">上一页</button>
   <button class="act ghost" ${page>=pages?'disabled':''} onclick="auditPage(1)">下一页</button>
  </div>`;
 }
 h+='</div>';
 box.innerHTML=h;
}
async function auditPage(d){
 AUDIT.page=Math.max(1,(AUDIT.page||1)+d);
 auditQuery(false);
}
async function auditFlags(){
 const st=g('au_flag_status')?g('au_flag_status').value:'';
 if(st!==lastFlagStatus) resetPage('audit_flags');
 lastFlagStatus=st;
 const r=await readPage('/api/audit/flags',{status:st},'audit_flags');
 AUDIT.flags=(r&&r.ok)?(r.items||[]):[];
 const sum=g('au_flag_sum'); if(sum) sum.textContent=`（待处理 ${r.open_count||0} 条）`;
 renderAuditFlags();
}
function renderAuditFlags(){
 const box=g('au_flags'); if(!box) return;
 const flags=AUDIT.flags||[];
 let h='';
 if(!flags.length){ h='<div class="empty">无异常标记</div>'; }
 else{
  h+='<table style="width:100%;border-collapse:collapse;font-size:12px">';
  h+='<tr style="text-align:left;color:#6b766c"><th style="padding:4px 6px">时间</th><th style="padding:4px 6px">类型</th><th style="padding:4px 6px">严重度</th><th style="padding:4px 6px">群</th><th style="padding:4px 6px">用户</th><th style="padding:4px 6px">描述</th><th style="padding:4px 6px">状态</th><th style="padding:4px 6px">操作</th></tr>';
  for(const f of flags){
   const sev=f.severity==='high'?'<span class="tag" style="color:#a54132">high</span>':(f.severity==='mid'?'<span class="tag" style="color:#9a6b1f">mid</span>':'<span class="tag">'+esc(f.severity||'')+'</span>');
   const stMap={open:'<span class="tag" style="color:#a54132">待处理</span>',handled:'<span class="tag" style="color:#1e6b45">已处理</span>',false_positive:'<span class="tag" style="color:#6b766c">误报</span>'};
   let ops='';
   if(f.status==='open'){
    ops=`<button class="act ghost" style="padding:2px 8px" onclick="auditFlagUpdate('${escA(f.flag_id)}','handled')">已处理</button> <button class="act ghost" style="padding:2px 8px" onclick="auditFlagUpdate('${escA(f.flag_id)}','false_positive')">误报</button>`;
   }
   h+=`<tr style="border-top:1px solid #e7e4d5">
    <td style="padding:5px 6px;white-space:nowrap">${fdate(f.ts)}</td>
    <td style="padding:5px 6px">${esc(f.type||'')}</td>
    <td style="padding:5px 6px">${sev}</td>
    <td style="padding:5px 6px">${esc(f.group||'')}</td>
    <td style="padding:5px 6px">${esc(f.pid||'')}</td>
    <td style="padding:5px 6px;max-width:300px;word-break:break-all">${esc(f.desc||'')}</td>
    <td style="padding:5px 6px">${stMap[f.status]||esc(f.status||'')}</td>
    <td style="padding:5px 6px;white-space:nowrap">${ops}</td>
   </tr>`;
  }
  h+='</table>';
 }
 box.innerHTML=h+pagerHTML('audit_flags');
}
async function auditScan(){
 if(!confirm('将全量扫描审计流水与转让记录，生成新的异常标记。确认继续？')) return;
 const msg=g('au_msg'); if(msg) msg.textContent='⏳ 扫描中…';
 const r=await api('/api/audit/scan',{});
 if(msg) msg.textContent='';
 if(r&&r.ok){
  alert(` 扫描完成：新增 ${r.new_flags||0} 条标记，共 ${r.total||0} 条，未处理 ${r.open_count||0} 条`);
  auditFlags();
 }else{
  alert(' 扫描失败：'+(r&&r.msg?r.msg:'无响应'));
 }
}
async function auditVerify(){
 const msg=g('au_msg'); if(msg) msg.textContent='⏳ 校验中…';
 const r=await api('/api/audit/verify',{});
 if(msg) msg.textContent='';
 if(!r) return alert(' 校验失败：无响应');
 if(r.ok){
  alert(` 哈希链完整：${r.checked||0} 条全部一致`);
 }else{
  alert(` 哈希链断链：第 ${r.broken_index||0} 条（时间 ${fdate(r.broken_ts)}）被篡改或丢失！`);
 }
}
async function auditFlagUpdate(fid,status){
 if(!confirm('确认将该标记标记为「'+((status==='handled')?'已处理':'误报')+'」？')) return;
 const r=await api('/api/audit/flag/update',{flag_id:fid,status:status});
 const msg=g('au_msg'); if(msg) msg.textContent=(r&&r.ok?' ':' ')+((r&&r.msg)||'');
 if(r&&r.ok) auditFlags();
}
async function testDeepSeek(){
 const msg=g('zy_test_msg'); if(msg) msg.textContent='⏳ 正在测试连接…';
 const r=await api('/api/zhongyuan/test_deepseek',{});
 if(msg) msg.textContent=r?(r.ok?' '+r.msg:' '+r.msg):' 测试失败：无响应';
}
async function testZhongyuanBroadcast(){
 if(!confirm('将向所有已注册群真实推送一条测试消息，确认继续？')) return;
 const msg=g('zy_test_msg'); if(msg) msg.textContent='⏳ 正在广播…';
 const r=await api('/api/zhongyuan/test_broadcast',{});
 if(msg) msg.textContent=r?(r.ok?' '+r.msg:' '+r.msg):' 广播失败：无响应';
}

async function testZhongyuanStart(){
 if(!confirm('将向所有已注册群真实推送「活动开始」通报（不更改活动状态），确认继续？')) return;
 const msg=g('zy_test_msg'); if(msg) msg.textContent='⏳ 正在推送「活动开始」通报…';
 const r=await api('/api/zhongyuan/test_start',{});
 if(msg) msg.textContent=r?(r.ok?' '+r.msg:' '+r.msg):' 推送失败：无响应';
}
async function testZhongyuanEnd(){
 if(!confirm('将向所有已注册群真实推送「活动结束」通报（不结算、不更改状态），确认继续？')) return;
 const msg=g('zy_test_msg'); if(msg) msg.textContent='⏳ 正在推送「活动结束」通报…';
 const r=await api('/api/zhongyuan/test_end',{});
 if(msg) msg.textContent=r?(r.ok?' '+r.msg:' '+r.msg):' 推送失败：无响应';
}
async function viewZhongyuanData(){
 const msg=g('zy_data_msg'); const box=g('zy_data_box');
 if(msg) msg.textContent='⏳ 正在读取中元数据…';
 const r=await api('/api/zhongyuan/data',{});
 if(!r){ if(msg) msg.textContent=' 读取失败：无响应'; return; }
 if(!r.ok){ if(msg) msg.textContent=' '+(r.msg||'读取失败'); return; }
 if(msg) msg.textContent=' 已读取：玩家 '+r.stats.players+' · 群 '+r.stats.groups+' · 进行中副本 '+r.stats.sessions;
 if(box){ box.value=JSON.stringify(r.data,null,2); box.style.display='block'; }
}
async function clearZhongyuanData(){
 if(!confirm(' 将清空中元所有玩家数据（玩家 / 群 / 副本 + 活动 ID 从 1 重新分配），配置保留。此操作不可撤销，确认继续？')) return;
 if(!confirm('再次确认：真的要删除当前中元全部玩家数据吗？')) return;
 const msg=g('zy_test_msg'); if(msg) msg.textContent='⏳ 正在清空…';
 const r=await api('/api/zhongyuan/clear_data',{});
 if(msg) msg.textContent=r?(r.ok?' '+r.msg:' '+r.msg):' 清空失败：无响应';
 if(r&&r.ok) viewZhongyuanData();
}

function shell(head,rows,cols){
 showPager(cur);
 document.getElementById('extrawrap').innerHTML='';
 document.getElementById('tablewrap').innerHTML = rows
   ? `<table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无数据</div>`;
}
function petEditInfo(v){
 // 多宠物系统：宠物统一存储在 v.pets 列表，返回要编辑的 (列表, active_pet 索引)；无宠物返回 (-1, null)
 const pets=Array.isArray(v.pets)?v.pets:null;
 if(!pets||!pets.length)return {idx:-1,pets:null};
 let idx=v.active_pet;
 if(!Number.isInteger(idx)||idx<0||idx>=pets.length)idx=0;
 return {idx:idx,pets:pets};
}
function renderPlayers(){
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];if(!match(k,v))continue;
  const pi=petEditInfo(v);
  const pet=(pi.idx>=0&&pi.pets[pi.idx])?`${esc(pi.pets[pi.idx].name||pi.pets[pi.idx].species||'宠物')}`:'—';
  const lv=(pi.idx>=0&&pi.pets[pi.idx])?('Lv'+(pi.pets[pi.idx].level||1)):'—';
  rows+=`<tr>
   <td>${esc(v.group||'')}</td><td class="num">${esc(v.qq||'')}</td>
   <td>${pet}</td><td class="num">${lv}</td>
   <td class="num coin">${v.coin||0}</td><td class="num jifen">${v.jifen||0}</td><td class="num diamond">${v.diamond||0}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 shell('<th>群号</th><th>QQ号</th><th>宠物</th><th>等级</th><th>灵石</th><th>玄晶</th><th>天晶</th><th>操作</th>',rows);
}
function renderGroups(){
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];if(!match(k,v))continue;
  rows+=`<tr><td class="k">${esc(k)}</td>
   <td><span class="tag ${v.enabled?'on':'off'}">${v.enabled?'已开启':'已关闭'}</span></td>
   <td><span class="tag ${v.cross?'on':'off'}">${v.cross?'允许':'禁止'}</span></td>
   <td class="num">${v.sign_count||0}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 shell('<th>群号</th><th>灵契仙途</th><th>跨群挑战</th><th>今日签到数</th><th>操作</th>',rows);
}
const CUR_CLS={'金币':'coin','积分':'jifen','钻石':'diamond'};
const DISP={'金币':'灵石','积分':'玄晶','钻石':'天晶'};const dsp=s=>DISP[s]||s;
function cardRewards(v){
 if(v.rewards&&typeof v.rewards==='object')return v.rewards;
 if(v.currency&&v.amount)return {[v.currency]:v.amount};
 return {};
}
function cardItems(v){
 if(v.items&&typeof v.items==='object')return v.items;
 return {};
}
function rewardsHtml(r){
 const parts=[];for(const c of ['金币','积分','钻石'])if(r[c])parts.push(`<span class="${CUR_CLS[c]}">${dsp(c)} +${r[c]}</span>`);
 return parts.length?parts.join(' ＋ '):'';
}
function itemsHtml(items){
 const parts=[];for(const [name,cnt] of Object.entries(items||{}))if(cnt>0)parts.push(`<span class="muted"> ${name} ×${cnt}</span>`);
 return parts.join(' ＋ ');
}
function packageHtml(v){
 const r=rewardsHtml(cardRewards(v));
 const i=itemsHtml(cardItems(v));
 const parts=[];if(r)parts.push(r);if(i)parts.push(i);
 return parts.length?parts.join(' ＋ '):'<span class="muted">—</span>';
}
function cardContentHtml(v){
 const acQuota=+(v.assistant_quota||0);
 if(acQuota>0)return `<span class="diamond"> 自动助手 ${acQuota} 次</span>`;
 if(v.mount_custom)return `<span class="diamond"> 坐骑外观定制卡</span>`;
 const days=+(v.auth_days||0);
 if(days>0)return `<span class="diamond"> 群授权 ${days} 天·${(v.server_type==='infinite'?'无限服':'官方服')}</span>`;
 return packageHtml(v);
}
function renderCards(){
 const stats=pageState('cards').stats||{};
 const total=stats.total||0,used=stats.used||0;
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];if(!match(k,v))continue;
  rows+=`<tr><td><input type="checkbox" class="cardchk" value="${esc(k)}"></td><td class="k">${esc(k)}</td>
   <td>${cardContentHtml(v)}</td>
   <td><span class="tag ${v.used?'used':'unused'}">${v.used?'已使用':'未使用'}</span></td>
   <td class="muted">${v.used_by?esc(v.used_by.replace(String.fromCharCode(31),' / ')):'—'}</td>
   <td class="muted">${fdate(v.created_at)}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 document.getElementById('cardstats').innerHTML=`<div class="stat"><div class="n">${total}</div><div class="l">卡密总数</div></div><div class="stat"><div class="n">${total-used}</div><div class="l">未使用</div></div><div class="stat"><div class="n">${used}</div><div class="l">已使用</div></div>`;
 shell('<th><input type="checkbox" aria-label="全选本页卡密" onclick="cardsToggleAll(this)"></th><th>卡密</th><th>套餐内容</th><th>状态</th><th>使用者</th><th>创建时间</th><th>操作</th>',rows);
}
function cardsToggleAll(box){document.querySelectorAll('.cardchk').forEach(c=>c.checked=box.checked);}
async function cardsDeleteSelected(){
 if(cur!=='cards'){alert('请先切换到卡密页');return;}
 const keys=[...document.querySelectorAll('.cardchk:checked')].map(c=>c.value);
 if(!keys.length){alert('请先勾选要删除的卡密');return;}
 if(!confirm('确认删除选中的 '+keys.length+' 个卡密？'))return;
 const r=await api('/api/cards/batch_delete',{keys});
 alert(r.ok?('已删除 '+r.deleted+' 个卡密'):(r.msg||'删除失败'));
 load();
}
async function cardsDeleteUsed(){
 if(cur!=='cards'){alert('请先切换到卡密页');return;}
 if(!confirm('确认删除全部已使用的卡密？'))return;
 const r=await api('/api/cards/batch_delete',{mode:'used'});
 alert(r.ok?('已删除 '+r.deleted+' 个已使用卡密'):(r.msg||'删除失败'));
 load();
}
function eventDate(ts){
 if(!ts)return '—';
 return new Date(ts*1000).toLocaleString('zh-CN',{hour12:false});
}
function renderEvents(){
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];if(!match(k,v))continue;
  const now=Math.floor(Date.now()/1000);
  const active=!!v.enabled && v.start_at<=now && now<=v.end_at;
  rows+=`<tr><td class="k">${esc(k)}</td>
   <td>${esc(v.name||'—')}</td>
   <td><span class="tag ${active?'on':'off'}">${active?'生效中':(v.enabled?'未生效':'已禁用')}</span></td>
   <td>${esc(v.token||'—')}</td>
   <td class="muted">${eventDate(v.start_at)}</td>
   <td class="muted">${eventDate(v.end_at)}</td>
   <td class="num">${Object.keys(v.actions||{}).length} / ${Object.keys(v.shop||{}).length} / ${((v.gacha||{}).pool||[]).length}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 shell('<th>ID</th><th>名称</th><th>状态</th><th>代币</th><th>开始</th><th>结束</th><th>玩法/商店/奖品</th><th>操作</th>',rows);
 document.getElementById('extrawrap').innerHTML=`
  <div class="sec" style="margin-top:14px">Boss 管理</div>
  <div class="bar" style="align-items:flex-end">
   <input id="boss_respawn_id" placeholder="活动ID" style="width:220px">
   <button class="act" onclick="bossRespawn()">立即复活该活动 Boss 并全服播报</button>
  </div>
  <div id="boss_respawn_msg" class="muted"></div>`;
}
function fieldHtml(){
 if(cur==='players')return `
  <div class="sec">基础</div>
  <div class="row"><div><label class="fld">灵石</label><input id="f_coin" type="number"></div>
  <div><label class="fld">玄晶</label><input id="f_jifen" type="number"></div>
  <div><label class="fld">天晶</label><input id="f_diamond" type="number"></div></div>
  <div class="row"><div><label class="fld">胜场</label><input id="f_st_win" type="number"></div>
  <div><label class="fld">探索次数</label><input id="f_st_exp" type="number"></div></div>
  <div class="sec">宠物</div><div id="petbox"></div>
  <div class="sec">背包</div><div id="bagbox"></div>
  <button class="act ghost" type="button" onclick="bagAdd()" style="margin-top:6px">＋ 添加物品</button>`;
 if(cur==='groups')return `
  <div class="sec">基础设置</div>
  <div class="chk"><input id="f_enabled" type="checkbox"><label for="f_enabled">开启灵契仙途</label></div>
  <div class="chk"><input id="f_cross" type="checkbox"><label for="f_cross">允许跨群挑战</label></div>`;
 if(cur==='events')return `
  <div class="muted">ID 保存后不可修改；活动时间选择本地日期，后台自动转时间戳。</div>
  <div class="row">
   <div style="flex:2"><label class="fld">活动名称</label><input id="f_name" placeholder="秋收冬藏"></div>
   <div><label class="fld">主题</label><input id="f_theme" placeholder="autumn"></div>
  </div>
  <div class="row">
   <div><label class="fld">菜单指令</label><input id="f_menu_cmd" placeholder="秋冬活动"></div>
   <div><label class="fld">代币名</label><input id="f_token" placeholder="银杏叶"></div>
  </div>
  <div class="row">
   <div><label class="fld">副本列表指令</label><input id="f_dungeon_list_cmd" placeholder="活动副本"></div>
   <div><label class="fld">进入副本指令</label><input id="f_dungeon_enter_cmd" placeholder="进入活动副本"></div>
  </div>
  <div class="chk"><input id="f_enabled" type="checkbox"><label for="f_enabled">启用</label></div>
  <div class="row">
   <div><label class="fld">开始时间</label><input id="f_start_at" type="datetime-local"></div>
   <div><label class="fld">结束时间</label><input id="f_end_at" type="datetime-local"></div>
  </div>
  <div class="sec">活动玩法</div>
  <div id="event_actions"></div>
  <button class="act ghost" type="button" onclick="eventAddAction()" style="margin-top:6px">＋ 添加玩法</button>
  <div class="sec">活动道具（自定义）</div>
  <div id="event_items"></div>
  <button class="act ghost" type="button" onclick="eventAddItem()" style="margin-top:6px">＋ 添加活动道具</button>
  <div class="sec">活动商店</div>
  <div id="event_shop"></div>
  <button class="act ghost" type="button" onclick="eventAddShop()" style="margin-top:6px">＋ 添加商品</button>
  <div class="sec">活动抽奖</div>
  <div class="chk"><input id="f_gacha_enabled" type="checkbox"><label for="f_gacha_enabled">启用抽奖</label></div>
  <div class="row">
   <div><label class="fld">抽奖指令</label><input id="f_gacha_cmd" placeholder="秋冬抽奖"></div>
   <div><label class="fld">每日次数</label><input id="f_gacha_limit" type="number" value="5"></div>
  </div>
  <div class="row"><div style="flex:1"><label class="fld">抽奖价格（如：贝壳 15）</label><input id="f_gacha_cost" placeholder="银杏叶 15"></div></div>
  <div class="sec" style="margin-top:10px">保底设置</div>
  <div class="chk"><input id="f_pity_enabled" type="checkbox"><label for="f_pity_enabled">启用大奖保底</label></div>
  <div class="row">
   <div style="flex:1"><label class="fld">大奖保底次数</label><input id="f_pity_big_threshold" type="number" value="1000" placeholder="如 1000"></div>
   <div style="flex:2"><label class="fld">大奖物品名（须与奖池奖品一致）</label><input id="f_pity_big_item" placeholder="如：史诗卡"></div>
  </div>
  <div class="row">
   <div style="flex:1"><label class="fld">小奖保底次数</label><input id="f_pity_small_threshold" type="number" value="500" placeholder="如 500"></div>
   <div style="flex:2"><label class="fld">小奖物品名（须与奖池奖品一致）</label><input id="f_pity_small_item" placeholder="如：灵石"></div>
  </div>
  <div id="event_gacha_pool"></div>
  <button class="act ghost" type="button" onclick="eventAddGacha()" style="margin-top:6px">＋ 添加奖品</button>
  <div class="sec">活动副本</div>
  <div id="event_dungeons"></div>
  <button class="act ghost" type="button" onclick="eventAddDungeon()" style="margin-top:6px">＋ 添加副本</button>
  <div class="sec">世界 Boss</div>
  <div class="chk"><input id="f_boss_enabled" type="checkbox"><label for="f_boss_enabled">启用世界 Boss</label></div>
  <div class="row">
   <div style="flex:1"><label class="fld">挑战指令</label><input id="f_boss_cmd" placeholder="秋冬Boss"></div>
   <div style="flex:1"><label class="fld">Boss名称</label><input id="f_boss_name" placeholder="丰收巨灵"></div>
   <div style="flex:1"><label class="fld">血量</label><input id="f_boss_hp" type="number" value="100000"></div>
  </div>
  <div class="row">
   <div style="flex:1"><label class="fld">等级要求</label><input id="f_boss_level" type="number" value="1"></div>
   <div style="flex:1"><label class="fld">宠物精力</label><input id="f_boss_energy" type="number" value="20"></div>
   <div style="flex:1"><label class="fld">冷却(秒)</label><input id="f_boss_cooldown" type="number" value="600"></div>
   <div style="flex:1"><label class="fld">每日次数</label><input id="f_boss_limit" type="number" value="5"></div>
  </div>
  <div class="row">
   <div style="flex:1"><label class="fld">伤害系数</label><input id="f_boss_factor" type="number" step="0.01" value="0.1"></div>
   <div style="flex:1"><label class="fld">每次代币</label><input id="f_boss_token_hit" type="number" value="5"></div>
   <div style="flex:1"><label class="fld">复活秒数</label><input id="f_boss_respawn" type="number" value="3600"></div>
   <div style="flex:1"><label class="fld">Boss攻击</label><input id="f_boss_damage" type="number" value="100" placeholder="每次反击宠物的基础伤害"></div>
  </div>
  <div class="chk" style="margin-top:8px"><input id="f_boss_random_damage" type="checkbox"><label for="f_boss_random_damage">玩家伤害随机（不跟宠物战力挂钩）</label></div>
  <div class="row">
   <div style="flex:1"><label class="fld">随机最小伤害</label><input id="f_boss_random_min" type="number" value="1"></div>
   <div style="flex:1"><label class="fld">随机最大伤害</label><input id="f_boss_random_max" type="number" value="10000"></div>
  </div>
  <div class="sec" style="margin-top:10px">击杀奖励（每条奖励都会发放，可设置随机数量）</div>
  <div id="event_boss_rewards"></div>
  <button class="act ghost" type="button" onclick="eventAddBossReward()" style="margin-top:6px">＋ 添加击杀奖励</button>
  <div class="muted" style="margin-top:10px">高级用户仍可在下方「高级编辑」中直接修改 JSON。表单保存时会覆盖表单内容到 JSON。</div>`;
 return `
  <div class="muted">套餐面额（空或 0 表示不含该项，可任意组合）；或填「授权天数」改为群授权卡。</div>
  <div class="row"><div><label class="fld">灵石</label><input id="f_r_coin" type="number"></div>
  <div><label class="fld">玄晶</label><input id="f_r_jifen" type="number"></div>
  <div><label class="fld">天晶</label><input id="f_r_diamond" type="number"></div>
  <div><label class="fld">授权天数(群授权卡)</label><input id="f_authdays" type="number"></div></div>
  <div class="chk"><input id="f_used" type="checkbox"><label for="f_used">已使用</label></div>`;
}
function buildPetForm(pet){
 const has=!!pet&&typeof pet==='object';const p=has?pet:{};
 let h=`<div class="chk"><input id="f_haspet" type="checkbox" ${has?'checked':''}><label for="f_haspet">拥有宠物（取消勾选并保存＝删除宠物；勾选无宠物者＝按默认值新建）</label></div><div class="row">`;
 for(const f of PET_FIELDS){const k=f[0],l=f[1],t=f[2],opt=f[3],empty=f[4];const val=p[k];let inp;
  if(t==='num')inp=`<input id="fp_${k}" type="number" value="${val!==undefined&&val!==null?escA(val):''}">`;
  else if(t==='sel')inp=`<select id="fp_${k}">${optHtml(META[opt],val,empty)}</select>`;
  else inp=`<input id="fp_${k}" value="${val!==undefined&&val!==null?escA(val):''}">`;
  h+=`<div style="min-width:115px;flex:1"><label class="fld">${l}</label>${inp}</div>`;}
 h+=`</div><div class="chk"><input id="fp_custom" type="checkbox" ${p.custom?'checked':''}><label for="fp_custom">定制宠物</label></div>`;
 const sk=p.skills||[];h+=`<label class="fld">秘技（按住 Ctrl/Cmd 多选）</label><select id="fp_skills" multiple style="height:96px;width:100%">`;
 for(const s of (META.skills||[]))h+=`<option ${sk.includes(s)?'selected':''}>${esc(s)}</option>`;
 for(const s of sk)if(!(META.skills||[]).includes(s))h+=`<option selected>${esc(s)}</option>`;
 h+=`</select>`;return h;
}
function bagRow(name,cnt){return `<div class="bagrow row" style="align-items:flex-end">
 <div style="flex:3"><input class="bagname" list="itemlist" value="${escA(name||'')}" placeholder="物品名"></div>
 <div style="flex:1"><input class="bagcnt" type="number" value="${cnt!==undefined&&cnt!==null?escA(cnt):1}" placeholder="数量"></div>
 <div style="flex:0"><button class="act del" type="button" onclick="this.closest('.bagrow').remove()">×</button></div></div>`;}
function buildBag(bag){bag=(bag&&typeof bag==='object')?bag:{};let h='';for(const n of Object.keys(bag))h+=bagRow(n,bag[n]);return h||'<div class="muted" id="bagempty">（空）</div>';}
function bagAdd(){const box=g('bagbox');const e=g('bagempty');if(e)e.remove();box.insertAdjacentHTML('beforeend',bagRow('',1));}
function fillFields(v){
 if(cur==='players'){
  g('f_coin').value=v.coin||0;g('f_jifen').value=v.jifen||0;g('f_diamond').value=v.diamond||0;
  const st=v.stats||{};g('f_st_win').value=st.battle_win||0;g('f_st_exp').value=st.explore||0;
  g('itemlist').innerHTML=(META.items||[]).map(i=>`<option value="${escA(i)}">`).join('');
  const pi=petEditInfo(v);
  g('petbox').innerHTML=buildPetForm(pi.idx>=0?pi.pets[pi.idx]:null);
  g('bagbox').innerHTML=buildBag(v.bag);
 }
 else if(cur==='groups'){
  g('f_enabled').checked=!!v.enabled;g('f_cross').checked=!!v.cross;
 }
 else if(cur==='events'){
  g('f_name').value=v.name||'';
  g('f_theme').value=v.theme||'';
  g('f_menu_cmd').value=v.menu_cmd||'';
  g('f_token').value=v.token||'';
  g('f_dungeon_list_cmd').value=v.dungeon_list_cmd||'';
  g('f_dungeon_enter_cmd').value=v.dungeon_enter_cmd||'';
  g('f_enabled').checked=!!v.enabled;
  g('f_start_at').value=eventTsToLocal(v.start_at||0);
  g('f_end_at').value=eventTsToLocal(v.end_at||0);
  const gc=v.gacha||{};
  g('f_gacha_enabled').checked=!!gc.enabled;
  g('f_gacha_cmd').value=gc.cmd||'';
  g('f_gacha_limit').value=gc.daily_limit!==undefined?gc.daily_limit:5;
  g('f_gacha_cost').value=eventCostToString(gc.cost||{});
  const pity=gc.pity||{};
  g('f_pity_enabled').checked=!!pity.enabled;
  const pityItems=(pity.items||[]);
  const big=pityItems.find(x=>x.name==='大奖保底')||{};
  const small=pityItems.find(x=>x.name==='小奖保底')||{};
  g('f_pity_big_threshold').value=big.threshold!==undefined?big.threshold:1000;
  g('f_pity_big_item').value=big.reward_item||'';
  g('f_pity_small_threshold').value=small.threshold!==undefined?small.threshold:500;
  g('f_pity_small_item').value=small.reward_item||'';
  eventRenderActions(v.actions||{});
  eventRenderItems(v.event_items||{});
  eventRenderShop(v.shop||{}, v.event_items||{});
  eventRenderGacha(gc.pool||[]);
  eventRenderDungeons(v.dungeons||{});
  const bs=v.boss||{};
  g('f_boss_enabled').checked=!!bs.enabled;
  g('f_boss_cmd').value=bs.cmd||'';
  g('f_boss_name').value=bs.name||'';
  g('f_boss_hp').value=bs.hp!==undefined?bs.hp:100000;
  g('f_boss_level').value=bs.level_req!==undefined?bs.level_req:1;
  g('f_boss_energy').value=bs.energy!==undefined?bs.energy:20;
  g('f_boss_cooldown').value=bs.cooldown!==undefined?bs.cooldown:600;
  g('f_boss_limit').value=bs.daily_limit!==undefined?bs.daily_limit:5;
  g('f_boss_factor').value=bs.damage_factor!==undefined?bs.damage_factor:0.1;
  g('f_boss_token_hit').value=bs.token_per_hit!==undefined?bs.token_per_hit:5;
  g('f_boss_respawn').value=bs.respawn_seconds!==undefined?bs.respawn_seconds:3600;
  g('f_boss_damage').value=bs.boss_damage!==undefined?bs.boss_damage:100;
  g('f_boss_random_damage').checked=!!bs.random_damage;
  g('f_boss_random_min').value=bs.random_damage_min!==undefined?bs.random_damage_min:1;
  g('f_boss_random_max').value=bs.random_damage_max!==undefined?bs.random_damage_max:10000;
  eventRenderBossRewards(bs.kill_rewards||[]);
 }
 else{const r=cardRewards(v);g('f_r_coin').value=r['金币']||'';g('f_r_jifen').value=r['积分']||'';g('f_r_diamond').value=r['钻石']||'';g('f_authdays').value=v.auth_days||'';g('f_used').checked=!!v.used;}
}
function applyFields(v){
 if(cur==='players'){
  v.coin=+g('f_coin').value||0;v.jifen=+g('f_jifen').value||0;v.diamond=+g('f_diamond').value||0;
  v.stats=v.stats||{};v.stats.battle_win=+g('f_st_win').value||0;v.stats.explore=+g('f_st_exp').value||0;
  const pi=petEditInfo(v);
  const haspet=g('f_haspet')&&g('f_haspet').checked;
  if(haspet){
   // 多宠物系统：宠物统一存于 v.pets[active_pet]，不写顶层 pet（运行时引用不落盘）
   const pets=pi.pets||(Array.isArray(v.pets)?v.pets:[]);
   let pet=(pi.idx>=0&&pets[pi.idx]&&typeof pets[pi.idx]==='object')?pets[pi.idx]:{};
   for(const f of PET_FIELDS){const k=f[0],t=f[2];const el=g('fp_'+k);if(!el)continue;
    if(t==='num'){if(el.value!=='')pet[k]=+el.value;}else{pet[k]=el.value;}}
   pet.custom=g('fp_custom').checked;
   pet.skills=Array.from(g('fp_skills').selectedOptions).map(o=>o.value);
   if(pet.artifact==='')pet.artifact=null;
   if(pet.talent==='')pet.talent=null;
   if(pet.love_target==='')pet.love_target=null;
   for(const k of Object.keys(PET_DEF))if(pet[k]===undefined)pet[k]=PET_DEF[k];
   if(!pet.created_at)pet.created_at=Math.floor(Date.now()/1000);
   if(!pet.last_energy_ts)pet.last_energy_ts=Math.floor(Date.now()/1000);
   if(pi.idx>=0){pets[pi.idx]=pet;}else{pets.push(pet);v.active_pet=pets.length-1;}
   v.pets=pets;
  }else if(pi.idx>=0){
   // 取消勾选＝删除当前活跃宠物
   pi.pets.splice(pi.idx,1);
   v.pets=pi.pets;
   v.active_pet=v.pets.length?Math.min(v.active_pet,v.pets.length-1):-1;
  }
  delete v.pet;  // 清理可能存在的幻影顶层 pet 字段
  const bag={};document.querySelectorAll('#bagbox .bagrow').forEach(r=>{const n=r.querySelector('.bagname').value.trim();const c=+r.querySelector('.bagcnt').value||0;if(n&&c>0)bag[n]=c;});
  v.bag=bag;
 }
 else if(cur==='groups'){
  v.enabled=g('f_enabled').checked;v.cross=g('f_cross').checked;
 }
 else if(cur==='events'){
  v.name=g('f_name').value.trim();
  v.theme=g('f_theme').value.trim();
  v.menu_cmd=g('f_menu_cmd').value.trim();
  v.token=g('f_token').value.trim();
  v.dungeon_list_cmd=g('f_dungeon_list_cmd').value.trim()||'活动副本';
  v.dungeon_enter_cmd=g('f_dungeon_enter_cmd').value.trim()||'进入活动副本';
  v.enabled=g('f_enabled').checked;
  const now=Math.floor(Date.now()/1000);
  v.start_at=eventLocalToTs(g('f_start_at').value)||now;
  v.end_at=eventLocalToTs(g('f_end_at').value)||(now+30*86400);
  v.actions=eventCollectActions();
  v.event_items=eventCollectItems();
  const rawShop=eventCollectShop();
  v.shop={};
  v.event_items=v.event_items||{};
  for(const [name,it] of Object.entries(rawShop)){
   const shopEff=it.effect||{};
   const existing=v.event_items[name]||{};
   const dedicatedEff=existing.effect||{};
   // 商店效果与独立活动道具效果同一概念：若两者不同，优先以商店编辑为准；否则保留独立区域的数据
   let finalEff=dedicatedEff;
   if(Object.keys(shopEff).length>0 && JSON.stringify(shopEff)!==JSON.stringify(dedicatedEff)){
    finalEff=shopEff;
   }
   v.shop[name]={cost:it.cost, stock:it.stock, desc:it.desc, effect:finalEff, reward:{item:name,count:1}};
   v.event_items[name]={
    category:existing.category||'道具',
    usable:Object.keys(finalEff).length>0?true:(existing.usable||false),
    desc:it.desc||existing.desc||'',
    effect:finalEff
   };
  }
  v.gacha={
   enabled:g('f_gacha_enabled').checked,
   cmd:g('f_gacha_cmd').value.trim()||'抽奖',
   daily_limit:+g('f_gacha_limit').value||0,
   cost:eventCostFromString(g('f_gacha_cost').value),
   pity:{
    enabled:g('f_pity_enabled').checked,
    items:[]
   },
   pool:eventCollectGacha()
  };
  if(g('f_pity_enabled').checked){
   const bigThreshold=+g('f_pity_big_threshold').value||0;
   const bigItem=g('f_pity_big_item').value.trim();
   if(bigThreshold>0 && bigItem){
    v.gacha.pity.items.push({name:'大奖保底',threshold:bigThreshold,reward_item:bigItem});
   }
   const smallThreshold=+g('f_pity_small_threshold').value||0;
   const smallItem=g('f_pity_small_item').value.trim();
   if(smallThreshold>0 && smallItem){
    v.gacha.pity.items.push({name:'小奖保底',threshold:smallThreshold,reward_item:smallItem});
   }
  }
  v.dungeons=eventCollectDungeons();
  v.boss={
   enabled:g('f_boss_enabled').checked,
   cmd:g('f_boss_cmd').value.trim()||'活动Boss',
   name:g('f_boss_name').value.trim()||'活动Boss',
   hp:+g('f_boss_hp').value||100000,
   level_req:+g('f_boss_level').value||1,
   energy:+g('f_boss_energy').value||0,
   cooldown:+g('f_boss_cooldown').value||600,
   daily_limit:+g('f_boss_limit').value||0,
   damage_factor:+g('f_boss_factor').value||0.1,
   token_per_hit:+g('f_boss_token_hit').value||0,
   respawn_seconds:+g('f_boss_respawn').value||3600,
   boss_damage:+g('f_boss_damage').value||100,
   random_damage:!!g('f_boss_random_damage').checked,
   random_damage_min:+g('f_boss_random_min').value||1,
   random_damage_max:+g('f_boss_random_max').value||10000,
   kill_rewards:eventCollectBossRewards()
  };
 }
 else{const ad=+g('f_authdays').value||0;if(ad>0){v.auth_days=ad;delete v.rewards;delete v.currency;delete v.amount;}else{const r={};const c=+g('f_r_coin').value||0,j=+g('f_r_jifen').value||0,d=+g('f_r_diamond').value||0;if(c>0)r['金币']=c;if(j>0)r['积分']=j;if(d>0)r['钻石']=d;v.rewards=r;delete v.currency;delete v.amount;delete v.auth_days;}v.used=g('f_used').checked;}
 return v;
}
function g(id){return document.getElementById(id);}
async function editRow(k){
 // 编辑前先拉取最新数据，并记录原始快照供保存时做乐观锁校验
 const table=cur;
 const [r]=await Promise.all([api('/api/list',{table,key:k}),ensureMeta()]);
 if(cur!==table)return;
 if(!(k in (r.data||{}))){alert('该记录已不存在');await load();return;}
 editSnapshot=JSON.parse(JSON.stringify(r.data[k]));
 openModal(k,JSON.parse(JSON.stringify(r.data[k])));
}
async function addRow(){const table=cur;await ensureMeta();if(cur!==table)return;editSnapshot=null;openModal('',{});}
function keyLabel(){return cur==='players'?'玩家键（群号\x1fQQ号）':cur==='groups'?'群号':cur==='events'?'活动ID':'卡密码';}
function openModal(k,v){
 editKey=k;
 g('mtitle').textContent=k?'编辑记录':'新增记录';
 g('mfields').innerHTML=(k?'':`<label class="fld">${keyLabel()}</label><input id="newkey" style="width:100%">`)+fieldHtml();
 g('msub').textContent=k?k:'';
 if(cur==='events' && !k){
  const now=Math.floor(Date.now()/1000);
  v={
   id:'',
   name:'秋收冬藏',
   enabled:true,
   start_at:now,
   end_at:now+30*86400,
   token:'银杏叶',
   theme:'autumn',
   menu_cmd:'秋冬活动',
   dungeon_list_cmd:'活动副本',
   dungeon_enter_cmd:'进入活动副本',
   actions:{
    '拾穗':{energy:10,cooldown:600,daily_limit:5,rewards:{银杏叶:{min:3,max:8,chance:1}},msg:' 你在田埂拾到 {银杏叶} 片银杏叶！'},
    '晒秋':{energy:15,cooldown:900,daily_limit:3,rewards:{银杏叶:{min:5,max:12,chance:1},经验:{min:50,max:120,chance:0.3}},msg:' 晒秋收获 {银杏叶} 片银杏叶！'}
   },
   event_items:{
    '桂花酿':{category:'药品',usable:true,desc:'暖心润体，恢复 200 点精力并回满心情。',effect:{heal_energy:200,mood:5}},
    '暖手炉':{category:'装饰',usable:false,desc:'秋冬活动限定装饰道具，可佩戴在宠物身上（收藏用）。',effect:{}},
    '丰收斗笠':{category:'道具',usable:true,desc:'戴上后永久增加 20 点攻击。',effect:{add_atk:20}}
   },
   shop:{
    '桂花酿':{cost:{银杏叶:20},stock:{per_player:5},reward:{item:'桂花酿',count:1},desc:'恢复 200 精力并回满心情'},
    '丰收斗笠':{cost:{银杏叶:80},stock:{per_player:1},reward:{effect:{add_atk:20}},desc:'永久攻击 +20'}
   },
   gacha:{enabled:true,cmd:'秋冬抽奖',cost:{银杏叶:15},daily_limit:5,
    pity:{enabled:true,items:[
     {name:'大奖保底',threshold:1000,reward_item:'史诗卡'},
     {name:'小奖保底',threshold:500,reward_item:'金币'}
    ]},
    pool:[
    {weight:38,reward:{银杏叶:5},msg:'安慰奖，拾得 5 片银杏叶'},
    {weight:20,reward:{item:'桂花酿',count:1},msg:'来杯桂花酿～'},
    {weight:12,reward:{金币:500}},
    {weight:8,reward:{item:'普通碎片',count:3},msg:'普通碎片×3'},
    {weight:7,reward:{item:'精品碎片',count:2},msg:'精品碎片×2'},
    {weight:6,reward:{item:'稀有碎片',count:1},msg:'稀有碎片×1'},
    {weight:5,reward:{effect:{add_hp_max:50}}},
    {weight:2,reward:{item:'传说碎片',count:1},msg:'传说碎片×1（稀）'},
    {weight:1,reward:{item:'史诗卡',count:1},msg:' 大奖！史诗卡'}
   ]},
   dungeons:{
    '珊瑚洞穴':{monster:'巨蟹守卫',level_req:10,energy:15,cooldown:600,power:1500,exp:315,jifen:180,token_reward:10,reward:{item:'桂花酿',count:1}},
    '沉船海湾':{monster:'幽灵船长',level_req:30,energy:25,cooldown:900,power:5000,exp:875,jifen:340,token_reward:25,reward:{item:'史诗卡',count:1}}
   },
   boss:{enabled:true,cmd:'秋冬Boss',name:'丰收巨灵',hp:100000,level_req:20,energy:30,cooldown:1800,daily_limit:3,damage_factor:0.1,token_per_hit:20,respawn_seconds:3600,boss_damage:200,random_damage:true,random_damage_min:1,random_damage_max:10000,kill_rewards:[
    {weight:48,reward:{银杏叶:100,银杏叶_max:200},msg:'满仓银杏叶'},
    {weight:30,reward:{item:'桂花酿',count:1,count_max:3}},
    {weight:15,reward:{effect:{add_atk:50}}},
    {weight:4,reward:{金币:1000,金币_max:5000}},
    {weight:2,reward:{item:'传说碎片',count:1,count_max:3},msg:'传说碎片×1~3'},
    {weight:1,reward:{item:'混沌卡',count:1},msg:' 混沌品质卡！'}
   ]}
  };
 }
 fillFields(v);
 g('mval').value=JSON.stringify(v,null,2);
 g('modal').style.display='flex';
}
async function saveRow(){
 let key=editKey;
 if(!key){const nk=g('newkey');key=nk?nk.value.trim():'';if(!key){alert('请填写键');return;}}
 let base;try{base=JSON.parse(g('mval').value||'{}');}catch(e){alert('高级 JSON 格式错误: '+e);return;}
 const v=applyFields(base);
 const payload={table:cur,key:key,value:v};
 if(editSnapshot!==null)payload.base=editSnapshot;
 const r=await api('/api/upsert',payload);
 if(!r.ok){alert(r.msg||'保存失败');load();return;}
 closeModal();load();
}
function closeModal(){g('modal').style.display='none';editKey=null;editSnapshot=null;}
async function delRow(k){if(!confirm('确认删除 '+k+' ?'))return;await api('/api/delete',{table:cur,key:k});load();}
async function genCards(){
 const cardType=g('card_type').value;
 let payload;
 if(cardType==='custom_pet'){
  payload={card_type:'custom_pet',count:+g('cnt').value,prefix:g('pre').value};
 }else if(cardType==='mount_custom'){
  payload={card_type:'mount_custom',count:+g('cnt').value,prefix:g('pre').value};
 }else if(cardType==='assistant'){
  payload={card_type:'assistant',count:+g('cnt').value,prefix:g('pre').value};
 }else{
  const authdays=+g('amt_authdays').value||0;
  if(authdays>0){
   payload={auth_days:authdays,server_type:g('amt_server_type').value||'official',count:+g('cnt').value,prefix:g('pre').value};
  }else{
   const rewards={};const c=+g('amt_coin').value||0,j=+g('amt_jifen').value||0,d=+g('amt_diamond').value||0;
   if(c>0)rewards['金币']=c;if(j>0)rewards['积分']=j;if(d>0)rewards['钻石']=d;
   const itemName=g('amt_item').value.trim();
   const itemCount=+g('amt_item_count').value||0;
   const items={};
   if(itemName&&itemCount>0)items[itemName]=itemCount;
   if(!Object.keys(rewards).length&&!Object.keys(items).length){alert('请填写灵石/玄晶/天晶面额，或选择道具及数量，或填写授权天数生成群授权卡');return;}
   payload={rewards:rewards,items:items,count:+g('cnt').value,prefix:g('pre').value};
  }
 }
 const r=await api('/api/cards/generate',payload);
 if(!r.ok){alert(r.msg||'生成失败');return;}
 g('genout').innerHTML=' 已生成 '+r.codes.length+' 张：<br>'+r.codes.map(esc).join('<br>');
 load();
}
function cardTypeChange(){
 const t=g('card_type').value;
 const hideRewards=(t==='custom_pet'||t==='mount_custom'||t==='assistant');
 ['amt_coin','amt_jifen','amt_diamond','amt_item','amt_item_count','amt_authdays','amt_server_type'].forEach(id=>{const el=g(id);if(el)el.style.display=hideRewards?'none':'';});
}
async function exportUnused(){
 const r=await api('/api/list',{table:'cards',export:'unused'});
 const exported=r.data||{};
 const lines=[];for(const k of Object.keys(exported)){const v=exported[k];if(v.used)continue;let pkg;if(+(v.assistant_quota||0)>0){pkg='自动助手'+v.assistant_quota+'次';}else if(v.mount_custom){pkg='坐骑外观定制';}else if(+(v.auth_days||0)>0){pkg='群授权'+v.auth_days+'天·'+(v.server_type==='infinite'?'无限服':'官方服');}else{const r=cardRewards(v);const items=cardItems(v);const parts=[];for(const c of ['金币','积分','钻石'])if(r[c])parts.push(dsp(c)+'+'+r[c]);for(const [name,cnt] of Object.entries(items||{}))if(cnt>0)parts.push(name+'×'+cnt);pkg=parts.join('/')||'空卡';}lines.push(`${k}\t${pkg}`);}
 if(!lines.length){alert('没有未使用的卡密');return;}
 const blob=new Blob([lines.join('\n')],{type:'text/plain'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='unused_cards.txt';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}

// ---- 活动编辑器辅助函数 ----
function eventTsToLocal(ts){
 if(!ts) return '';
 const d=new Date(ts*1000);
 d.setMinutes(d.getMinutes()-d.getTimezoneOffset());
 return d.toISOString().slice(0,16);
}
function eventLocalToTs(s){
 if(!s) return 0;
 return Math.floor(new Date(s).getTime()/1000);
}
function eventCostToString(cost){
 if(!cost || typeof cost!=='object') return '';
 return Object.entries(cost).map(([k,v])=>k+' '+v).join(' / ');
}
function eventCostFromString(s){
 const out={};
 if(!s) return out;
 for(const part of s.split('/')){
  const m=part.trim().match(/^(.+?)\s+(\d+)$/);
  if(m) out[m[1].trim()]=+m[2];
 }
 return out;
}
function eventRewardHtml(reward){
 reward=reward||{};
 let type='item';
 if(reward.effect!==undefined) type='effect';
 else if(reward.item!==undefined) type='item';
 else {
  const k=Object.keys(reward).find(x=>x!=='msg' && !x.endsWith('_max'));
  if(k && META.currencies && META.currencies.includes(k)) type='currency';
  else if(k) type='token';
 }
 let detail='';
 if(type==='item'){
  detail=`<div class="row"><div style="flex:2"><label>物品名</label><input class="ev-r-item" list="ev-item-datalist" value="${escA(reward.item||'')}" placeholder="输入或选择道具名"></div><div style="flex:1"><label>最小数量</label><input class="ev-r-count" type="number" value="${reward.count!==undefined?reward.count:1}"></div><div style="flex:1"><label>最大数量</label><input class="ev-r-count-max" type="number" value="${reward.count_max!==undefined?reward.count_max:''}" placeholder="固定"></div></div>`;
 } else if(type==='effect'){
  const eff=reward.effect||{};
  const k=Object.keys(eff)[0]||'add_atk';
  const v=Object.values(eff)[0]||0;
  detail=`<div class="row"><div style="flex:2"><label>效果键</label><select class="ev-r-effk">${['add_atk','add_def','add_intel','add_hp_max','add_energy_max','mood','heal_hp','heal_energy','add_exp'].map(o=>`<option ${o===k?'selected':''}>${o}</option>`).join('')}</select></div><div style="flex:1"><label>数值</label><input class="ev-r-effv" type="number" value="${v}"></div></div>`;
 } else if(type==='currency'){
  const k=Object.keys(reward).find(x=>META.currencies.includes(x))||'金币';
  const v=reward[k]||0;
  const vmax=reward[k+'_max'];
  detail=`<div class="row"><div style="flex:2"><label>货币</label><select class="ev-r-cur">${META.currencies.map(o=>`<option value="${o}" ${o===k?'selected':''}>${dsp(o)}</option>`).join('')}</select></div><div style="flex:1"><label>最小值</label><input class="ev-r-curv" type="number" value="${v}"></div><div style="flex:1"><label>最大值</label><input class="ev-r-curv-max" type="number" value="${vmax!==undefined?vmax:''}" placeholder="固定"></div></div>`;
 } else if(type==='token'){
  const k=Object.keys(reward).find(x=>!META.currencies.includes(x)&&x!=='msg'&&!x.endsWith('_max'))||'';
  const v=reward[k]||0;
  const vmax=reward[k+'_max'];
  detail=`<div class="row"><div style="flex:2"><label>代币名</label><input class="ev-r-tok" value="${escA(k)}"></div><div style="flex:1"><label>最小值</label><input class="ev-r-tokv" type="number" value="${v}"></div><div style="flex:1"><label>最大值</label><input class="ev-r-tokv-max" type="number" value="${vmax!==undefined?vmax:''}" placeholder="固定"></div></div>`;
 }
 return `<div class="ev-reward" data-type="${type}"><div class="row"><div style="flex:1"><label>奖励类型</label><select class="ev-r-type" onchange="eventRewardTypeChange(this)">${[['item','物品'],['effect','属性'],['currency','货币'],['token','活动代币']].map(([t,l])=>`<option value="${t}" ${t===type?'selected':''}>${l}</option>`).join('')}</select></div></div><div class="ev-r-detail">${detail}</div></div>`;
}
function eventRewardTypeChange(sel){
 const box=sel.closest('.ev-reward');
 const type=sel.value;
 box.dataset.type=type;
 let detail='';
 if(type==='item') detail=`<div class="row"><div style="flex:2"><label>物品名</label><input class="ev-r-item" list="ev-item-datalist" value="" placeholder="输入或选择道具名"></div><div style="flex:1"><label>最小数量</label><input class="ev-r-count" type="number" value="1"></div><div style="flex:1"><label>最大数量</label><input class="ev-r-count-max" type="number" value="" placeholder="固定"></div></div>`;
 else if(type==='effect') detail=`<div class="row"><div style="flex:2"><label>效果键</label><select class="ev-r-effk">${['add_atk','add_def','add_intel','add_hp_max','add_energy_max','mood','heal_hp','heal_energy','add_exp'].map(o=>`<option>${o}</option>`).join('')}</select></div><div style="flex:1"><label>数值</label><input class="ev-r-effv" type="number" value="0"></div></div>`;
 else if(type==='currency') detail=`<div class="row"><div style="flex:2"><label>货币</label><select class="ev-r-cur">${(META.currencies||['金币','积分','钻石']).map(o=>`<option value="${o}">${dsp(o)}</option>`).join('')}</select></div><div style="flex:1"><label>最小值</label><input class="ev-r-curv" type="number" value="0"></div><div style="flex:1"><label>最大值</label><input class="ev-r-curv-max" type="number" value="" placeholder="固定"></div></div>`;
 else if(type==='token') detail=`<div class="row"><div style="flex:2"><label>代币名</label><input class="ev-r-tok" value=""></div><div style="flex:1"><label>最小值</label><input class="ev-r-tokv" type="number" value="0"></div><div style="flex:1"><label>最大值</label><input class="ev-r-tokv-max" type="number" value="" placeholder="固定"></div></div>`;
 box.querySelector('.ev-r-detail').innerHTML=detail;
}
function eventCollectReward(box){
 const type=box.dataset.type || box.querySelector('.ev-r-type').value;
 if(type==='item'){
  const name=box.querySelector('.ev-r-item').value.trim();
  const count=+box.querySelector('.ev-r-count').value||1;
  const countMax=+box.querySelector('.ev-r-count-max').value||0;
  if(!name) return null;
  const out={item:name,count:count};
  if(countMax>count) out.count_max=countMax;
  return out;
 } else if(type==='effect'){
  const k=box.querySelector('.ev-r-effk').value;
  const v=+box.querySelector('.ev-r-effv').value||0;
  return {effect:{[k]:v}};
 } else if(type==='currency'){
  const k=box.querySelector('.ev-r-cur').value;
  const min=+box.querySelector('.ev-r-curv').value||0;
  const max=+box.querySelector('.ev-r-curv-max').value||0;
  const out={[k]:min};
  if(max>min) out[k+'_max']=max;
  return out;
 } else if(type==='token'){
  const k=box.querySelector('.ev-r-tok').value.trim();
  const min=+box.querySelector('.ev-r-tokv').value||0;
  const max=+box.querySelector('.ev-r-tokv-max').value||0;
  if(!k) return null;
  const out={[k]:min};
  if(max>min) out[k+'_max']=max;
  return out;
 }
 return null;
}

// actions
function eventActionHtml(name,conf){
 conf=conf||{};
 return `<div class="event-card" style="border:1px solid #d8d7c9;padding:10px;margin:8px 0;border-radius:4px">
  <div class="row">
   <div style="flex:2"><label>玩法指令</label><input class="ev-a-name" value="${escA(name)}"></div>
   <div style="flex:1"><label>宠物精力</label><input class="ev-a-energy" type="number" value="${conf.energy!==undefined?conf.energy:10}"></div>
   <div style="flex:1"><label>冷却(秒)</label><input class="ev-a-cooldown" type="number" value="${conf.cooldown!==undefined?conf.cooldown:600}"></div>
   <div style="flex:1"><label>每日次数</label><input class="ev-a-limit" type="number" value="${conf.daily_limit!==undefined?conf.daily_limit:5}" placeholder="空=不限"></div>
  </div>
  <div style="margin-top:6px"><label>结果文案（可用 {代币名} 占位）</label><input class="ev-a-msg" style="width:100%" value="${escA(conf.msg||'')}"></div>
  <div class="sec" style="margin-top:10px">随机奖励</div>
  <div class="ev-a-rewards"></div>
  <button class="act ghost" type="button" onclick="eventAddReward(this.closest('.event-card').querySelector('.ev-a-rewards'))" style="margin-top:6px">＋ 奖励</button>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove()" style="margin-top:6px">删除玩法</button>
 </div>`;
}
function eventAddAction(){
 const box=g('event_actions');
 const div=document.createElement('div');
 div.innerHTML=eventActionHtml('',{});
 const card=div.firstElementChild;
 box.appendChild(card);
 eventAddReward(card.querySelector('.ev-a-rewards'));
}
function eventRenderActions(actions){
 const box=g('event_actions'); box.innerHTML='';
 for(const [name,conf] of Object.entries(actions||{})){
  const div=document.createElement('div');
  div.innerHTML=eventActionHtml(name,conf);
  const card=div.firstElementChild;
  box.appendChild(card);
  eventRenderRewards(card.querySelector('.ev-a-rewards'),conf.rewards||{});
 }
}
function eventCollectActions(){
 const out={};
 document.querySelectorAll('#event_actions .event-card').forEach(card=>{
  const name=card.querySelector('.ev-a-name').value.trim();
  if(!name) return;
  const limit=card.querySelector('.ev-a-limit').value;
  out[name]={
   energy:+card.querySelector('.ev-a-energy').value||0,
   cooldown:+card.querySelector('.ev-a-cooldown').value||0,
   daily_limit:limit===''?null:+limit,
   msg:card.querySelector('.ev-a-msg').value,
   rewards:eventCollectRewards(card.querySelector('.ev-a-rewards'))
  };
 });
 return out;
}
function eventAddReward(container){
 const div=document.createElement('div');
 div.innerHTML=`<div class="reward-row row" style="align-items:flex-end;margin:6px 0;border:1px dashed #4f5c50;padding:8px;border-radius:6px">
   <div style="flex:2"><label>奖励名</label><input class="ev-r-name" value="" placeholder="贝壳 / 经验 / 物品名"></div>
   <div style="flex:1"><label>最小值</label><input class="ev-r-min" type="number" value="0"></div>
   <div style="flex:1"><label>最大值</label><input class="ev-r-max" type="number" value="0"></div>
   <div style="flex:1"><label>概率</label><input class="ev-r-chance" type="number" step="0.1" value="1"></div>
   <div style="flex:0"><button class="act del" type="button" onclick="this.closest('.reward-row').remove()">×</button></div>
  </div>`;
 container.appendChild(div.firstElementChild);
}
function eventRenderRewards(container,rewards){
 container.innerHTML='';
 for(const [name,cfg] of Object.entries(rewards||{})){
  const div=document.createElement('div');
  div.innerHTML=`<div class="reward-row row" style="align-items:flex-end;margin:6px 0;border:1px dashed #4f5c50;padding:8px;border-radius:6px">
   <div style="flex:2"><label>奖励名</label><input class="ev-r-name" value="${escA(name)}" placeholder="贝壳 / 经验 / 物品名"></div>
   <div style="flex:1"><label>最小值</label><input class="ev-r-min" type="number" value="${cfg.min!==undefined?cfg.min:0}"></div>
   <div style="flex:1"><label>最大值</label><input class="ev-r-max" type="number" value="${cfg.max!==undefined?cfg.max:0}"></div>
   <div style="flex:1"><label>概率</label><input class="ev-r-chance" type="number" step="0.1" value="${cfg.chance!==undefined?cfg.chance:1}"></div>
   <div style="flex:0"><button class="act del" type="button" onclick="this.closest('.reward-row').remove()">×</button></div>
  </div>`;
  container.appendChild(div.firstElementChild);
 }
}
function eventCollectRewards(container){
 const out={};
 container.querySelectorAll('.reward-row').forEach(row=>{
  const name=row.querySelector('.ev-r-name').value.trim();
  if(!name) return;
  out[name]={
   min:+row.querySelector('.ev-r-min').value||0,
   max:+row.querySelector('.ev-r-max').value||0,
   chance:+row.querySelector('.ev-r-chance').value||1
  };
 });
 return out;
}

// shop
function eventShopHtml(name,it){
 it=it||{};
 return `<div class="event-card" style="border:1px solid #d8d7c9;padding:10px;margin:8px 0;border-radius:4px">
  <div class="row">
   <div style="flex:2"><label>商品名</label><input class="ev-s-name" value="${escA(name)}"></div>
   <div style="flex:2"><label>价格（如：贝壳 20 / 灵石 100）</label><input class="ev-s-cost" value="${escA(eventCostToString(it.cost||{}))}"></div>
   <div style="flex:1"><label>每人限购</label><input class="ev-s-per" type="number" value="${it.stock&&it.stock.per_player!==undefined?it.stock.per_player:''}" placeholder="空=不限"></div>
   <div style="flex:1"><label>全局库存</label><input class="ev-s-global" type="number" value="${it.stock&&it.stock.global!==undefined?it.stock.global:''}" placeholder="空=不限"></div>
  </div>
  <div style="margin-top:6px"><label>描述</label><input class="ev-s-desc" style="width:100%" value="${escA(it.desc||'')}"></div>
  <div class="sec" style="margin-top:10px">道具使用效果（购买后获得该道具）</div>
  <div class="ev-s-effect">${eventItemEffectHtml(it.effect||{})}</div>
  <button class="act ghost" type="button" onclick="eventAddEffRow(this)" style="margin-top:6px">＋ 添加效果</button>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove()" style="margin-top:6px">删除商品</button>
 </div>`;
}
function eventAddShop(){
 const box=g('event_shop');
 const div=document.createElement('div');
 div.innerHTML=eventShopHtml('',{});
 box.appendChild(div.firstElementChild);
}
function eventRenderShop(shop,event_items){
 event_items=event_items||{};
 const box=g('event_shop'); box.innerHTML='';
 for(const [name,it] of Object.entries(shop||{})){
  // 使用效果统一存到 event_items，shop 自身可能没 effect；这里优先回显 event_items
  let effect=it.effect||{};
  if(!Object.keys(effect).length){
   const ei=event_items[name];
   if(ei && ei.effect && typeof ei.effect==='object') effect=ei.effect;
  }
  if(!Object.keys(effect).length && it.reward && it.reward.effect && typeof it.reward.effect==='object'){
   effect=it.reward.effect;
  }
  const div=document.createElement('div');
  div.innerHTML=eventShopHtml(name,{...it,effect:effect});
  box.appendChild(div.firstElementChild);
 }
}
function eventCollectShop(){
 const out={};
 document.querySelectorAll('#event_shop .event-card').forEach(card=>{
  const name=card.querySelector('.ev-s-name').value.trim();
  if(!name) return;
  const effect={};
  card.querySelectorAll('.ev-s-effect .ev-eff-row').forEach(row=>{
   const k=row.querySelector('.ev-eff-k').value;
   const v=+row.querySelector('.ev-eff-v').value||0;
   if(k) effect[k]=v;
  });
  const it={cost:eventCostFromString(card.querySelector('.ev-s-cost').value), desc:card.querySelector('.ev-s-desc').value, effect:effect, reward:{item:name,count:1}};
  const per=card.querySelector('.ev-s-per').value;
  const glob=card.querySelector('.ev-s-global').value;
  it.stock={};
  if(per!=='') it.stock.per_player=+per;
  if(glob!=='') it.stock.global=+glob;
  out[name]=it;
 });
 return out;
}


async function bossRespawn(){
 const eid=g('boss_respawn_id').value.trim();
 const box=g('boss_respawn_msg');
 if(!eid){box.textContent='请输入活动ID';return;}
 const r=await api('/api/boss_respawn',{event_id:eid});
 box.textContent=r.ok?(r.msg||'操作成功'):(r.msg||'操作失败');
 if(r.ok) load();
}

// gacha
function eventGachaHtml(entry){
 entry=entry||{};
 return `<div class="event-card" style="border:1px solid #d8d7c9;padding:10px;margin:8px 0;border-radius:4px">
  <div class="row">
   <div style="flex:1"><label>权重</label><input class="ev-g-weight" type="number" value="${entry.weight!==undefined?entry.weight:1}"></div>
   <div style="flex:3"><label>提示文案（可选）</label><input class="ev-g-msg" value="${escA(entry.msg||'')}" placeholder="例如：恭喜获得大奖！"></div>
  </div>
  <div class="sec" style="margin-top:10px">奖品内容</div>
  <div class="ev-g-reward">${eventRewardHtml(entry.reward||{})}</div>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove()" style="margin-top:6px">删除奖品</button>
 </div>`;
}
function eventAddGacha(){
 const box=g('event_gacha_pool');
 const div=document.createElement('div');
 div.innerHTML=eventGachaHtml({});
 box.appendChild(div.firstElementChild);
}
function eventRenderGacha(pool){
 const box=g('event_gacha_pool'); box.innerHTML='';
 for(const entry of (pool||[])){
  const div=document.createElement('div');
  div.innerHTML=eventGachaHtml(entry);
  box.appendChild(div.firstElementChild);
 }
}
function eventCollectGacha(){
 const out=[];
 document.querySelectorAll('#event_gacha_pool .event-card').forEach(card=>{
  const rw=eventCollectReward(card.querySelector('.ev-g-reward .ev-reward'));
  if(!rw) return;
  out.push({weight:+card.querySelector('.ev-g-weight').value||1, msg:card.querySelector('.ev-g-msg').value, reward:rw});
 });
 return out;
}

// dungeons
function eventDungeonHtml(name,conf){
 conf=conf||{};
 return `<div class="event-card" style="border:1px solid #d8d7c9;padding:10px;margin:8px 0;border-radius:4px">
  <div class="row">
   <div style="flex:2"><label>副本名称</label><input class="ev-d-name" value="${escA(name)}"></div>
   <div style="flex:2"><label>怪物名</label><input class="ev-d-monster" value="${escA(conf.monster||'')}"/></div>
   <div style="flex:1"><label>等级要求</label><input class="ev-d-level" type="number" value="${conf.level_req!==undefined?conf.level_req:1}"></div>
  </div>
  <div class="row">
   <div style="flex:1"><label>宠物精力</label><input class="ev-d-energy" type="number" value="${conf.energy!==undefined?conf.energy:10}"></div>
   <div style="flex:1"><label>冷却(秒)</label><input class="ev-d-cooldown" type="number" value="${conf.cooldown!==undefined?conf.cooldown:600}"></div>
   <div style="flex:1"><label>每日次数</label><input class="ev-d-limit" type="number" value="${conf.daily_limit!==undefined?conf.daily_limit:''}" placeholder="空=不限"></div>
   <div style="flex:1"><label>怪物战力</label><input class="ev-d-power" type="number" value="${conf.power!==undefined?conf.power:1000}"></div>
  </div>
  <div class="row">
   <div style="flex:1"><label title="推荐≈(100+等级×80)×0.35，避免一次副本连升数级">经验</label><input class="ev-d-exp" type="number" value="${conf.exp!==undefined?conf.exp:0}"></div>
   <div style="flex:1"><label title="推荐≈100+等级×8">玄晶</label><input class="ev-d-jifen" type="number" value="${conf.jifen!==undefined?conf.jifen:0}"></div>
   <div style="flex:1"><label>代币奖励</label><input class="ev-d-token" type="number" value="${conf.token_reward!==undefined?conf.token_reward:0}"></div>
  </div>
  <div class="sec" style="margin-top:10px">通关额外奖励（可选）</div>
  <div class="ev-d-reward">${eventRewardHtml(conf.reward||{})}</div>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove()" style="margin-top:6px">删除副本</button>
 </div>`;
}
function eventAddDungeon(){
 const box=g('event_dungeons');
 const div=document.createElement('div');
 div.innerHTML=eventDungeonHtml('',{});
 box.appendChild(div.firstElementChild);
}
function eventRenderDungeons(dungeons){
 const box=g('event_dungeons'); box.innerHTML='';
 for(const [name,conf] of Object.entries(dungeons||{})){
  const div=document.createElement('div');
  div.innerHTML=eventDungeonHtml(name,conf);
  box.appendChild(div.firstElementChild);
 }
}
function eventCollectDungeons(){
 const out={};
 document.querySelectorAll('#event_dungeons .event-card').forEach(card=>{
  const name=card.querySelector('.ev-d-name').value.trim();
  if(!name) return;
  const limit=card.querySelector('.ev-d-limit').value;
  out[name]={
   monster:card.querySelector('.ev-d-monster').value.trim()||'怪物',
   level_req:+card.querySelector('.ev-d-level').value||1,
   energy:+card.querySelector('.ev-d-energy').value||0,
   cooldown:+card.querySelector('.ev-d-cooldown').value||600,
   power:+card.querySelector('.ev-d-power').value||0,
   exp:+card.querySelector('.ev-d-exp').value||0,
   jifen:+card.querySelector('.ev-d-jifen').value||0,
   token_reward:+card.querySelector('.ev-d-token').value||0,
   reward:eventCollectReward(card.querySelector('.ev-d-reward .ev-reward'))
  };
  if(limit!=='') out[name].daily_limit=+limit;
 });
 return out;
}

// event items
const EVENT_ITEM_EFFECT_KEYS=['add_atk','add_def','add_intel','add_hp_max','add_energy_max','mood','heal_hp','heal_energy','add_exp'];
function eventItemEffectRowHtml(k,v){
 return `<div class="ev-eff-row row" style="align-items:flex-end;margin:4px 0">
  <div style="flex:2"><label>效果键</label><select class="ev-eff-k">${EVENT_ITEM_EFFECT_KEYS.map(o=>`<option ${o===k?'selected':''}>${o}</option>`).join('')}</select></div>
  <div style="flex:1"><label>数值</label><input class="ev-eff-v" type="number" value="${v!==undefined?v:0}"></div>
  <div style="flex:0"><button class="act del" type="button" onclick="this.closest('.ev-eff-row').remove()">×</button></div>
 </div>`;
}
function eventItemEffectHtml(effect){
 effect=effect||{};
 // 兼容旧版 {effect:{heal_energy:200}} 包裹格式
 if(effect.effect && typeof effect.effect==='object') effect=effect.effect;
 const keys=Object.keys(effect);
 if(keys.length===0) return '';
 return keys.map(k=>eventItemEffectRowHtml(k,effect[k])).join('');
}
function eventAddEffRow(btn){
 const box=btn.previousElementSibling;
 if(!box) return;
 const div=document.createElement('div');
 div.innerHTML=eventItemEffectRowHtml('heal_energy',0);
 box.appendChild(div.firstElementChild);
}
function eventItemHtml(name,conf){
 conf=conf||{};
 return `<div class="event-card" style="border:1px solid #d8d7c9;padding:10px;margin:8px 0;border-radius:4px">
  <div class="row">
   <div style="flex:2"><label>道具名</label><input class="ev-i-name" value="${escA(name)}" placeholder="夏日冰饮"></div>
   <div style="flex:1"><label>分类</label><select class="ev-i-cat">${['药品','道具','装饰','材料'].map(o=>`<option ${o===(conf.category||'道具')?'selected':''}>${o}</option>`).join('')}</select></div>
   <div style="flex:0"><div class="chk" style="margin-top:20px"><input class="ev-i-usable" type="checkbox" ${conf.usable?'checked':''}><label>可使用</label></div></div>
  </div>
  <div style="margin-top:6px"><label>描述</label><input class="ev-i-desc" style="width:100%" value="${escA(conf.desc||'')}"></div>
  <div class="sec" style="margin-top:10px">使用效果</div>
  <div class="ev-i-effect">${eventItemEffectHtml(conf.effect)}</div>
  <button class="act ghost" type="button" onclick="eventAddEffRow(this)" style="margin-top:6px">＋ 添加效果</button>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove();updateEventItemDatalist();" style="margin-top:6px">删除道具</button>
 </div>`;
}
function eventAddItem(){
 const box=g('event_items');
 const div=document.createElement('div');
 div.innerHTML=eventItemHtml('',{});
 box.appendChild(div.firstElementChild);
 updateEventItemDatalist();
}
function eventRenderItems(items){
 const box=g('event_items'); box.innerHTML='';
 for(const [name,conf] of Object.entries(items||{})){
  // 兼容旧版包裹格式
  if(conf.effect && conf.effect.effect && typeof conf.effect.effect==='object') conf.effect=conf.effect.effect;
  const div=document.createElement('div');
  div.innerHTML=eventItemHtml(name,conf);
  box.appendChild(div.firstElementChild);
 }
 updateEventItemDatalist();
}
function eventCollectItems(){
 const out={};
 document.querySelectorAll('#event_items .event-card').forEach(card=>{
  const name=card.querySelector('.ev-i-name').value.trim();
  if(!name) return;
  const effect={};
  card.querySelectorAll('.ev-i-effect .ev-eff-row').forEach(row=>{
   const k=row.querySelector('.ev-eff-k').value;
   const v=+row.querySelector('.ev-eff-v').value||0;
   if(k) effect[k]=v;
  });
  out[name]={
   category:card.querySelector('.ev-i-cat').value,
   usable:!!card.querySelector('.ev-i-usable').checked,
   desc:card.querySelector('.ev-i-desc').value,
   effect:effect
  };
 });
 return out;
}
function updateEventItemDatalist(){
 let dl=g('ev-item-datalist');
 if(!dl){
  dl=document.createElement('datalist');
  dl.id='ev-item-datalist';
  document.body.appendChild(dl);
 }
 const names=new Set(META.items||[]);
 document.querySelectorAll('#event_items .ev-i-name').forEach(el=>{const v=el.value.trim();if(v)names.add(v);});
 dl.innerHTML=Array.from(names).map(i=>`<option value="${escA(i)}">`).join('');
}

// boss
function eventBossRewardHtml(entry){
 entry=entry||{};
 return `<div class="event-card" style="border:1px solid #d8d7c9;padding:10px;margin:8px 0;border-radius:4px">
  <div class="row">
   <div style="flex:1"><label>分配权重（越高越优先给高伤害）</label><input class="ev-b-weight" type="number" value="${entry.weight!==undefined?entry.weight:1}"></div>
   <div style="flex:3"><label>提示文案（可选）</label><input class="ev-b-msg" value="${escA(entry.msg||'')}" placeholder="例如：恭喜获得大奖！"></div>
  </div>
  <div class="sec" style="margin-top:10px">奖励内容（设置最小/最大数量即可随机）</div>
  <div class="ev-b-reward">${eventRewardHtml(entry.reward||{})}</div>
  <button class="act del" type="button" onclick="this.closest('.event-card').remove()" style="margin-top:6px">删除奖励</button>
 </div>`;
}
function eventAddBossReward(){
 const box=g('event_boss_rewards');
 const div=document.createElement('div');
 div.innerHTML=eventBossRewardHtml({});
 box.appendChild(div.firstElementChild);
}
function eventRenderBossRewards(rewards){
 const box=g('event_boss_rewards'); box.innerHTML='';
 for(const entry of (rewards||[])){
  const div=document.createElement('div');
  div.innerHTML=eventBossRewardHtml(entry);
  box.appendChild(div.firstElementChild);
 }
}
function eventCollectBossRewards(){
 const out=[];
 document.querySelectorAll('#event_boss_rewards .event-card').forEach(card=>{
  const rw=eventCollectReward(card.querySelector('.ev-b-reward .ev-reward'));
  if(!rw) return;
  out.push({weight:+card.querySelector('.ev-b-weight').value||1, msg:card.querySelector('.ev-b-msg').value, reward:rw});
 });
 return out;
}

updateHeading(cur);
load().catch(reportError);
