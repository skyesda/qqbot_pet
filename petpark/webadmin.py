"""宠物乐园专属管理网站。

在 AstrBot 进程内启动一个独立端口的 aiohttp 网站，提供：
- 账号密码登录（默认 admin / 2468080asd，可在插件配置修改）；
- 查看 / 增删改查 插件数据库（玩家 players、群设置 groups、卡密 cards）；
- 批量生成卡密（金币 / 积分 / 钻石）。

依赖 aiohttp（AstrBot 自带）。启动失败不会影响插件主体功能。
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from astrbot.api import logger

COOKIE = "pp_session"
TABLES = ("players", "groups", "cards")


class WebAdmin:
    def __init__(self, store, host: str, port: int, user: str, password: str):
        self.store = store
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self._tokens: set[str] = set()
        self._runner = None

    # --------------------------- 生命周期 ---------------------------
    async def start(self) -> None:
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/login", self._login_page)
        app.router.add_post("/login", self._login_submit)
        app.router.add_get("/logout", self._logout)
        app.router.add_post("/api/list", self._api_list)
        app.router.add_post("/api/upsert", self._api_upsert)
        app.router.add_post("/api/delete", self._api_delete)
        app.router.add_post("/api/cards/generate", self._api_gen_cards)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self._runner = runner
        logger.info(
            f"[petpark] 管理网站已启动: http://{self.host}:{self.port} "
            f"(账号 {self.user})"
        )

    async def stop(self) -> None:
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                pass
            self._runner = None

    # --------------------------- 鉴权 ---------------------------
    def _authed(self, request) -> bool:
        return request.cookies.get(COOKIE) in self._tokens

    def _require(self, request):
        from aiohttp import web

        if not self._authed(request):
            raise web.HTTPFound("/login")

    async def _login_page(self, request):
        from aiohttp import web

        return web.Response(text=LOGIN_HTML, content_type="text/html")

    async def _login_submit(self, request):
        from aiohttp import web

        data = await request.post()
        if (
            data.get("user") == self.user
            and data.get("password") == self.password
        ):
            token = secrets.token_hex(16)
            self._tokens.add(token)
            resp = web.HTTPFound("/")
            resp.set_cookie(COOKIE, token, httponly=True, max_age=86400)
            return resp
        return web.Response(
            text=LOGIN_HTML.replace("<!--ERR-->", "账号或密码错误"),
            content_type="text/html",
        )

    async def _logout(self, request):
        from aiohttp import web

        tok = request.cookies.get(COOKIE)
        self._tokens.discard(tok)
        resp = web.HTTPFound("/login")
        resp.del_cookie(COOKIE)
        return resp

    async def _index(self, request):
        from aiohttp import web

        self._require(request)
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    # --------------------------- API ---------------------------
    @staticmethod
    def _table(name: str) -> str:
        if name not in TABLES:
            from aiohttp import web

            raise web.HTTPBadRequest(text="未知数据表")
        return name

    def _json(self, payload: Any):
        from aiohttp import web

        return web.json_response(payload, dumps=lambda o: json.dumps(o, ensure_ascii=False))

    async def _api_list(self, request):
        self._require(request)
        body = await request.json()
        table = self._table(body.get("table", ""))
        return self._json({"ok": True, "data": self.store._data.get(table, {})})

    async def _api_upsert(self, request):
        self._require(request)
        body = await request.json()
        table = self._table(body.get("table", ""))
        key = str(body.get("key", "")).strip()
        value = body.get("value")
        if not key:
            return self._json({"ok": False, "msg": "键不能为空"})
        if not isinstance(value, dict):
            return self._json({"ok": False, "msg": "记录内容必须是 JSON 对象"})
        self.store._data.setdefault(table, {})[key] = value
        await self.store.save()
        return self._json({"ok": True})

    async def _api_delete(self, request):
        self._require(request)
        body = await request.json()
        table = self._table(body.get("table", ""))
        key = str(body.get("key", ""))
        self.store._data.get(table, {}).pop(key, None)
        await self.store.save()
        return self._json({"ok": True})

    async def _api_gen_cards(self, request):
        self._require(request)
        body = await request.json()
        try:
            codes = self.store.create_cards(
                currency=body.get("currency", ""),
                amount=int(body.get("amount", 0)),
                count=int(body.get("count", 1)),
                prefix=body.get("prefix", ""),
            )
        except (ValueError, TypeError) as e:
            return self._json({"ok": False, "msg": str(e)})
        await self.store.save()
        return self._json({"ok": True, "codes": codes})


LOGIN_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>宠物乐园 · 管理登录</title>
<style>
body{margin:0;font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0f172a;color:#e2e8f0;display:flex;min-height:100vh;align-items:center;justify-content:center}
.box{background:#1e293b;padding:32px;border-radius:16px;width:320px;box-shadow:0 10px 40px rgba(0,0,0,.4)}
h1{font-size:20px;margin:0 0 20px;text-align:center}
input{width:100%;box-sizing:border-box;padding:11px;margin:8px 0;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:14px}
button{width:100%;padding:11px;margin-top:12px;border:0;border-radius:8px;background:#6366f1;color:#fff;font-size:15px;cursor:pointer}
button:hover{background:#4f46e5}
.err{color:#f87171;text-align:center;min-height:18px;font-size:13px}
</style></head><body>
<form class="box" method="post" action="/login">
<h1>🐾 宠物乐园 · 管理后台</h1>
<div class="err"><!--ERR--></div>
<input name="user" placeholder="账号" autocomplete="username">
<input name="password" type="password" placeholder="密码" autocomplete="current-password">
<button type="submit">登录</button>
</form></body></html>"""



DASHBOARD_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>宠物乐园 · 管理后台</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0f172a;color:#e2e8f0}
header{background:#1e293b;padding:14px 20px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:10}
header h1{font-size:17px;margin:0;flex:1}
header a{color:#94a3b8;text-decoration:none;font-size:14px}
.tabs{display:flex;gap:8px;padding:14px 20px 0}
.tabs button{padding:9px 18px;border:0;border-radius:10px 10px 0 0;background:#1e293b;color:#94a3b8;cursor:pointer;font-size:14px}
.tabs button.active{background:#334155;color:#fff;font-weight:600}
main{padding:16px 20px}
.cards-stat{display:flex;gap:12px;margin-bottom:14px;flex-wrap:wrap}
.stat{background:#1e293b;border-radius:12px;padding:12px 18px;min-width:120px}
.stat .n{font-size:22px;font-weight:700}
.stat .l{font-size:12px;color:#94a3b8;margin-top:2px}
.bar{margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input,select{padding:9px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:13px}
label.fld{display:block;margin:10px 0 4px;font-size:13px;color:#94a3b8}
button.act{padding:9px 15px;border:0;border-radius:8px;background:#6366f1;color:#fff;cursor:pointer;font-size:13px}
button.act:hover{background:#4f46e5}
button.del{background:#dc2626}button.del:hover{background:#b91c1c}
button.ghost{background:#475569}button.ghost:hover{background:#64748b}
table{width:100%;border-collapse:collapse;font-size:13px;background:#1e293b;border-radius:12px}
th,td{padding:11px 12px;border-bottom:1px solid #334155;text-align:left}
th{color:#94a3b8;background:#172033;font-weight:600}
tr:hover td{background:#243047}
td.k{font-family:monospace;color:#a5b4fc;word-break:break-all;max-width:240px}
.num{font-variant-numeric:tabular-nums}
.coin{color:#fbbf24}.jifen{color:#34d399}.diamond{color:#22d3ee}
.tag{padding:2px 9px;border-radius:999px;font-size:12px;white-space:nowrap}
.used{background:#7f1d1d;color:#fecaca}.unused{background:#14532d;color:#bbf7d0}
.on{background:#1e3a8a;color:#bfdbfe}.off{background:#374151;color:#cbd5e1}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:20}
.modal .card{background:#1e293b;padding:22px;border-radius:14px;width:min(560px,94vw);max-height:90vh;overflow:auto}
.modal h3{margin:0 0 6px}
.row{display:flex;gap:10px;flex-wrap:wrap}
.row>div{flex:1;min-width:120px}
.row input{width:100%}
textarea{width:100%;height:240px;font-family:monospace;font-size:13px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;padding:10px}
.muted{color:#64748b;font-size:12px}
.adv{margin-top:12px}
.adv summary{cursor:pointer;color:#94a3b8;font-size:13px}
.chk{display:flex;align-items:center;gap:8px;margin:10px 0}
.chk input{width:auto}
.empty{padding:24px;text-align:center;color:#64748b}
</style></head><body>
<header><h1>🐾 宠物乐园 · 管理后台</h1><a href="/logout">退出登录</a></header>
<div class="tabs">
<button data-t="players" class="active" onclick="tab('players')">玩家</button>
<button data-t="groups" onclick="tab('groups')">群设置</button>
<button data-t="cards" onclick="tab('cards')">卡密</button>
</div>
<main>
<div id="cardgen" style="display:none">
<div class="cards-stat" id="cardstats"></div>
<div class="bar">
<select id="cur"><option>金币</option><option>积分</option><option>钻石</option></select>
<input id="amt" type="number" placeholder="面额(如 10000)" style="width:150px">
<input id="cnt" type="number" placeholder="数量" value="10" style="width:90px">
<input id="pre" placeholder="前缀(可选,如VIP)" style="width:140px">
<button class="act" onclick="genCards()">批量生成</button>
<button class="ghost act" onclick="exportUnused()">导出未用卡密</button>
</div>
<div id="genout" class="muted" style="margin-bottom:8px"></div>
</div>
<div class="bar">
<button class="act" onclick="addRow()">＋ 新增</button>
<input id="q" placeholder="搜索…" oninput="render()" style="flex:1;min-width:160px">
<button class="ghost act" onclick="load()">刷新</button>
<span class="muted" id="count"></span>
</div>
<div id="tablewrap"></div>
</main>
<div class="modal" id="modal"><div class="card">
<h3 id="mtitle">编辑</h3>
<div class="muted" id="msub"></div>
<div id="mfields"></div>
<details class="adv"><summary>高级编辑（原始 JSON）</summary>
<textarea id="mval"></textarea></details>
<div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
<button class="act ghost" onclick="closeModal()">取消</button>
<button class="act" onclick="saveRow()">保存</button>
</div></div></div>
<script>
let cur='players', cache={}, editKey=null;
function tab(t){cur=t;document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.t===t));document.getElementById('cardgen').style.display=(t==='cards')?'block':'none';load();}
async function api(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json();}
async function load(){const r=await api('/api/list',{table:cur});cache=r.data||{};render();}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function tj(k){return JSON.stringify(k);}
function fdate(ts){if(!ts)return '—';const d=new Date(ts*1000);return d.toLocaleString('zh-CN',{hour12:false});}
function match(k,v){const q=(document.getElementById('q').value||'').toLowerCase();if(!q)return true;return k.toLowerCase().includes(q)||JSON.stringify(v).toLowerCase().includes(q);}
function render(){
 if(cur==='players')renderPlayers();
 else if(cur==='groups')renderGroups();
 else renderCards();
}
function shell(head,rows,cols){
 document.getElementById('count').textContent='共 '+Object.keys(cache).length+' 条';
 document.getElementById('tablewrap').innerHTML = rows
   ? `<table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`
   : `<div class="empty">暂无数据</div>`;
}
function renderPlayers(){
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];if(!match(k,v))continue;
  const pet=v.pet?`${esc(v.pet.name||v.pet.species||'宠物')}`:'—';
  const lv=v.pet?('Lv'+(v.pet.level||1)):'—';
  rows+=`<tr>
   <td>${esc(v.group||'')}</td><td class="num">${esc(v.qq||'')}</td>
   <td>${pet}</td><td class="num">${lv}</td>
   <td class="num coin">${v.coin||0}</td><td class="num jifen">${v.jifen||0}</td><td class="num diamond">${v.diamond||0}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 shell('<th>群号</th><th>QQ号</th><th>宠物</th><th>等级</th><th>金币</th><th>积分</th><th>钻石</th><th>操作</th>',rows);
}
function renderGroups(){
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];if(!match(k,v))continue;
  rows+=`<tr><td class="k">${esc(k)}</td>
   <td><span class="tag ${v.enabled?'on':'off'}">${v.enabled?'已开启':'已关闭'}</span></td>
   <td><span class="tag ${v.cross?'on':'off'}">${v.cross?'允许':'禁止'}</span></td>
   <td class="num">${v.sign_count||0}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 shell('<th>群号</th><th>宠物乐园</th><th>跨群挑战</th><th>今日签到数</th><th>操作</th>',rows);
}
function renderCards(){
 let total=0,used=0;
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];total++;if(v.used)used++;if(!match(k,v))continue;
  rows+=`<tr><td class="k">${esc(k)}</td>
   <td>${esc(v.currency||'')}</td><td class="num">${v.amount||0}</td>
   <td><span class="tag ${v.used?'used':'unused'}">${v.used?'已使用':'未使用'}</span></td>
   <td class="muted">${v.used_by?esc(v.used_by.replace(String.fromCharCode(31),' / ')):'—'}</td>
   <td class="muted">${fdate(v.created_at)}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 document.getElementById('cardstats').innerHTML=`<div class="stat"><div class="n">${total}</div><div class="l">卡密总数</div></div><div class="stat"><div class="n">${total-used}</div><div class="l">未使用</div></div><div class="stat"><div class="n">${used}</div><div class="l">已使用</div></div>`;
 shell('<th>卡密</th><th>货币</th><th>面额</th><th>状态</th><th>使用者</th><th>创建时间</th><th>操作</th>',rows);
}
function fieldHtml(){
 if(cur==='players')return `
  <div class="row"><div><label class="fld">金币</label><input id="f_coin" type="number"></div>
  <div><label class="fld">积分</label><input id="f_jifen" type="number"></div>
  <div><label class="fld">钻石</label><input id="f_diamond" type="number"></div></div>
  <div class="row"><div><label class="fld">宠物等级(无宠物则忽略)</label><input id="f_petlevel" type="number"></div></div>`;
 if(cur==='groups')return `
  <div class="chk"><input id="f_enabled" type="checkbox"><label for="f_enabled">开启宠物乐园</label></div>
  <div class="chk"><input id="f_cross" type="checkbox"><label for="f_cross">允许跨群挑战</label></div>`;
 return `
  <div class="row"><div><label class="fld">货币</label><select id="f_currency"><option>金币</option><option>积分</option><option>钻石</option></select></div>
  <div><label class="fld">面额</label><input id="f_amount" type="number"></div></div>
  <div class="chk"><input id="f_used" type="checkbox"><label for="f_used">已使用</label></div>`;
}
function fillFields(v){
 if(cur==='players'){g('f_coin').value=v.coin||0;g('f_jifen').value=v.jifen||0;g('f_diamond').value=v.diamond||0;g('f_petlevel').value=v.pet?(v.pet.level||1):'';}
 else if(cur==='groups'){g('f_enabled').checked=!!v.enabled;g('f_cross').checked=!!v.cross;}
 else{g('f_currency').value=v.currency||'金币';g('f_amount').value=v.amount||0;g('f_used').checked=!!v.used;}
}
function applyFields(v){
 if(cur==='players'){v.coin=+g('f_coin').value||0;v.jifen=+g('f_jifen').value||0;v.diamond=+g('f_diamond').value||0;if(v.pet&&g('f_petlevel').value!=='')v.pet.level=+g('f_petlevel').value;}
 else if(cur==='groups'){v.enabled=g('f_enabled').checked;v.cross=g('f_cross').checked;}
 else{v.currency=g('f_currency').value;v.amount=+g('f_amount').value||0;v.used=g('f_used').checked;}
 return v;
}
function g(id){return document.getElementById(id);}
function editRow(k){openModal(k,JSON.parse(JSON.stringify(cache[k]||{})));}
function addRow(){openModal('',{});}
function keyLabel(){return cur==='players'?'玩家键（群号\\x1fQQ号）':cur==='groups'?'群号':'卡密码';}
function openModal(k,v){
 editKey=k;
 g('mtitle').textContent=k?'编辑记录':'新增记录';
 g('mfields').innerHTML=(k?'':`<label class="fld">${keyLabel()}</label><input id="newkey" style="width:100%">`)+fieldHtml();
 g('msub').textContent=k?k:'';
 fillFields(v);
 g('mval').value=JSON.stringify(v,null,2);
 g('modal').style.display='flex';
}
async function saveRow(){
 let key=editKey;
 if(!key){const nk=g('newkey');key=nk?nk.value.trim():'';if(!key){alert('请填写键');return;}}
 let base;try{base=JSON.parse(g('mval').value||'{}');}catch(e){alert('高级 JSON 格式错误: '+e);return;}
 const v=applyFields(base);
 const r=await api('/api/upsert',{table:cur,key:key,value:v});
 if(!r.ok){alert(r.msg||'保存失败');return;}
 closeModal();load();
}
function closeModal(){g('modal').style.display='none';editKey=null;}
async function delRow(k){if(!confirm('确认删除 '+k+' ?'))return;await api('/api/delete',{table:cur,key:k});load();}
async function genCards(){
 const body={currency:g('cur').value,amount:+g('amt').value,count:+g('cnt').value,prefix:g('pre').value};
 if(!body.amount){alert('请填写面额');return;}
 const r=await api('/api/cards/generate',body);
 if(!r.ok){alert(r.msg||'生成失败');return;}
 g('genout').innerHTML='✅ 已生成 '+r.codes.length+' 张：<br>'+r.codes.map(esc).join('<br>');
 load();
}
function exportUnused(){
 const lines=[];for(const k of Object.keys(cache)){const v=cache[k];if(!v.used)lines.push(`${k}\\t${v.currency}\\t${v.amount}`);}
 if(!lines.length){alert('没有未使用的卡密');return;}
 const blob=new Blob([lines.join('\\n')],{type:'text/plain'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='unused_cards.txt';a.click();
}
load();
</script></body></html>"""
