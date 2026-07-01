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
import time
from typing import Any

from astrbot.api import logger

COOKIE = "pp_session"
TABLES = ("players", "groups", "cards", "events")


class WebAdmin:
    def __init__(
        self,
        store,
        host: str,
        port: int,
        user: str,
        password: str,
        broadcast_callback=None,
    ):
        self.store = store
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self._broadcast_callback = broadcast_callback
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
        app.router.add_post("/api/meta", self._api_meta)
        app.router.add_post("/api/upsert", self._api_upsert)
        app.router.add_post("/api/delete", self._api_delete)
        app.router.add_post("/api/cards/generate", self._api_gen_cards)
        app.router.add_post("/api/boss_respawn", self._api_boss_respawn)

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

    async def _api_meta(self, request):
        """返回各类枚举值，供前端编辑表单渲染下拉框。"""
        self._require(request)
        from . import data

        return self._json(
            {
                "ok": True,
                "data": {
                    "species": list(data.SPECIES.keys()),
                    "qualities": list(data.QUALITIES),
                    "elements": list(data.ELEMENTS),
                    "genders": ["男", "女"],
                    "stages": list(data.STAGES),
                    "statuses": list(data.STATUSES),
                    "love_states": list(data.LOVE_STATES),
                    "artifacts": list(data.ARTIFACTS.keys()),
                    "talents": list(data.TALENTS.keys()),
                    "skills": list(data.SKILLS.keys()),
                    "items": list(data.ITEMS.keys()),
                    "currencies": ["金币", "积分", "钻石"],
                },
            }
        )

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
        # 保存活动时保留运行时 Boss 状态，避免后台编辑把当前血量/伤害排行清空
        if table == "events":
            existing = self.store._data.get(table, {}).get(key)
            if isinstance(existing, dict) and "_boss_state" in existing \
                    and "_boss_state" not in value:
                value["_boss_state"] = existing["_boss_state"]
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
        auth_days = int(body.get("auth_days", 0) or 0)
        try:
            if auth_days > 0:
                codes = self.store.create_auth_cards(
                    days=auth_days,
                    count=int(body.get("count", 1)),
                    prefix=body.get("prefix", ""),
                )
            else:
                rewards = body.get("rewards")
                if not isinstance(rewards, dict):
                    # 兼容旧版单一货币入参
                    rewards = {body.get("currency", ""): body.get("amount", 0)}
                codes = self.store.create_combo_cards(
                    rewards=rewards,
                    count=int(body.get("count", 1)),
                    prefix=body.get("prefix", ""),
                )
        except (ValueError, TypeError) as e:
            return self._json({"ok": False, "msg": str(e)})
        await self.store.save()
        return self._json({"ok": True, "codes": codes})


    async def _api_boss_respawn(self, request):
        """管理后台：立即复活指定活动的 Boss，并向所有授权群播报。

        仅在 Boss 已阵亡（处于复活倒计时中）时允许立即复活；Boss 还活着时不能强制复活。
        """
        self._require(request)
        body = await request.json()
        eid = str(body.get("event_id", "")).strip()
        if not eid:
            return self._json({"ok": False, "msg": "请填写活动ID"})
        cfg = self.store.events().get(eid)
        if not cfg:
            return self._json({"ok": False, "msg": f"活动 {eid} 不存在"})
        boss = cfg.get("boss", {})
        if not boss.get("enabled"):
            return self._json({"ok": False, "msg": "该活动未启用 Boss"})

        state = cfg.get("_boss_state", {})
        now = int(time.time())
        # 判断 Boss 是否还活着：有血且不在复活倒计时中
        if (
            state
            and state.get("hp", 0) > 0
            and state.get("respawn_until", 0) <= now
        ):
            return self._json(
                {"ok": False, "msg": "Boss 还活着，无需复活。请等待它被击杀后再操作。"}
            )

        max_hp = int(boss.get("hp", 10000))
        cfg["_boss_state"] = {
            "max_hp": max_hp,
            "hp": max_hp,
            "respawn_until": 0,
            "damage_rank": {},
            "respawn_notified": False,
        }
        await self.store.save()
        bname = boss.get("name", "活动Boss")
        cmd = boss.get("cmd", "活动Boss")
        text = (
            f"## 👹 世界 Boss {bname} 已复活！\n"
            f"血量 {max_hp}/{max_hp}，发送 `{cmd}` 即可挑战。"
        )
        if self._broadcast_callback:
            try:
                self._broadcast_callback(text)
            except Exception:
                logger.exception("[petpark] 后台复活 Boss 广播失败")
        return self._json({"ok": True, "msg": f"Boss {bname} 已复活并全服播报"})


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
.modal .card{background:#1e293b;padding:22px;border-radius:14px;width:min(720px,96vw);max-height:90vh;overflow:auto}
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
.sec{margin:16px 0 6px;font-weight:700;font-size:14px;color:#c7d2fe;border-bottom:1px solid #334155;padding-bottom:4px}
.bagrow{margin:6px 0}
.empty{padding:24px;text-align:center;color:#64748b}
</style></head><body>
<header><h1>🐾 宠物乐园 · 管理后台</h1><a href="/logout">退出登录</a></header>
<div class="tabs">
<button data-t="players" class="active" onclick="tab('players')">玩家</button>
<button data-t="groups" onclick="tab('groups')">群设置</button>
<button data-t="cards" onclick="tab('cards')">卡密</button>
<button data-t="events" onclick="tab('events')">活动</button>
</div>
<main>
<div id="cardgen" style="display:none">
<div class="cards-stat" id="cardstats"></div>
<div class="muted" style="margin-bottom:6px">套餐卡密：填了哪几项就加哪几项，可任意组合（金币+钻石、金币+积分、三种一起…）。空或 0 表示不含该项。<b>填写「授权天数」则生成群授权卡（忽略货币）。</b></div>
<div class="bar">
<input id="amt_coin" type="number" placeholder="金币面额" style="width:120px">
<input id="amt_jifen" type="number" placeholder="积分面额" style="width:120px">
<input id="amt_diamond" type="number" placeholder="钻石面额" style="width:120px">
<input id="amt_authdays" type="number" placeholder="授权天数(群授权卡)" style="width:160px">
<input id="cnt" type="number" placeholder="数量" value="10" style="width:80px">
<input id="pre" placeholder="前缀(可选,如VIP)" style="width:130px">
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
<div id="extrawrap"></div>
<div id="tablewrap"></div>
</main>
<div class="modal" id="modal"><div class="card">
<h3 id="mtitle">编辑</h3>
<div class="muted" id="msub"></div>
<datalist id="itemlist"></datalist>
<div id="mfields"></div>
<details class="adv"><summary>高级编辑（原始 JSON）</summary>
<textarea id="mval"></textarea></details>
<div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
<button class="act ghost" onclick="closeModal()">取消</button>
<button class="act" onclick="saveRow()">保存</button>
</div></div></div>
<script>
let cur='players', cache={}, editKey=null, META={};
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
async function loadMeta(){try{const r=await api('/api/meta',{});META=r.data||{};}catch(e){META={};}}
function escA(s){return esc(s).replace(/"/g,'&quot;');}
function optHtml(list,val,empty){let h='';const L=(list||[]).map(String);if(empty!==undefined)h+=`<option value="">${esc(empty)}</option>`;for(const o of L)h+=`<option ${String(o)===String(val)?'selected':''}>${esc(o)}</option>`;if(val!==undefined&&val!==null&&val!==''&&!L.includes(String(val)))h+=`<option selected>${esc(val)}</option>`;return h;}
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
 else if(cur==='events')renderEvents();
 else renderCards();
}
function shell(head,rows,cols){
 document.getElementById('count').textContent='共 '+Object.keys(cache).length+' 条';
 document.getElementById('extrawrap').innerHTML='';
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
const CUR_CLS={'金币':'coin','积分':'jifen','钻石':'diamond'};
function cardRewards(v){
 if(v.rewards&&typeof v.rewards==='object')return v.rewards;
 if(v.currency&&v.amount)return {[v.currency]:v.amount};
 return {};
}
function rewardsHtml(r){
 const parts=[];for(const c of ['金币','积分','钻石'])if(r[c])parts.push(`<span class="${CUR_CLS[c]}">${c} +${r[c]}</span>`);
 return parts.length?parts.join(' ＋ '):'<span class="muted">—</span>';
}
function cardContentHtml(v){
 const days=+(v.auth_days||0);
 if(days>0)return `<span class="diamond">🔐 群授权 ${days} 天</span>`;
 return rewardsHtml(cardRewards(v));
}
function renderCards(){
 let total=0,used=0;
 let rows='';
 for(const k of Object.keys(cache)){const v=cache[k];total++;if(v.used)used++;if(!match(k,v))continue;
  rows+=`<tr><td class="k">${esc(k)}</td>
   <td>${cardContentHtml(v)}</td>
   <td><span class="tag ${v.used?'used':'unused'}">${v.used?'已使用':'未使用'}</span></td>
   <td class="muted">${v.used_by?esc(v.used_by.replace(String.fromCharCode(31),' / ')):'—'}</td>
   <td class="muted">${fdate(v.created_at)}</td>
   <td style="white-space:nowrap"><button class="act" onclick='editRow(${tj(k)})'>编辑</button> <button class="act del" onclick='delRow(${tj(k)})'>删除</button></td></tr>`;}
 document.getElementById('cardstats').innerHTML=`<div class="stat"><div class="n">${total}</div><div class="l">卡密总数</div></div><div class="stat"><div class="n">${total-used}</div><div class="l">未使用</div></div><div class="stat"><div class="n">${used}</div><div class="l">已使用</div></div>`;
 shell('<th>卡密</th><th>套餐内容</th><th>状态</th><th>使用者</th><th>创建时间</th><th>操作</th>',rows);
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
  <div class="row"><div><label class="fld">金币</label><input id="f_coin" type="number"></div>
  <div><label class="fld">积分</label><input id="f_jifen" type="number"></div>
  <div><label class="fld">钻石</label><input id="f_diamond" type="number"></div></div>
  <div class="row"><div><label class="fld">胜场</label><input id="f_st_win" type="number"></div>
  <div><label class="fld">探索次数</label><input id="f_st_exp" type="number"></div></div>
  <div class="sec">宠物</div><div id="petbox"></div>
  <div class="sec">背包</div><div id="bagbox"></div>
  <button class="act ghost" type="button" onclick="bagAdd()" style="margin-top:6px">＋ 添加物品</button>`;
 if(cur==='groups')return `
  <div class="chk"><input id="f_enabled" type="checkbox"><label for="f_enabled">开启宠物乐园</label></div>
  <div class="chk"><input id="f_cross" type="checkbox"><label for="f_cross">允许跨群挑战</label></div>`;
 if(cur==='events')return `
  <div class="muted">ID 保存后不可修改；活动时间选择本地日期，后台自动转时间戳。</div>
  <div class="row">
   <div style="flex:2"><label class="fld">活动名称</label><input id="f_name" placeholder="清凉一夏"></div>
   <div><label class="fld">主题</label><input id="f_theme" placeholder="summer"></div>
  </div>
  <div class="row">
   <div><label class="fld">菜单指令</label><input id="f_menu_cmd" placeholder="夏日活动"></div>
   <div><label class="fld">代币名</label><input id="f_token" placeholder="贝壳"></div>
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
   <div><label class="fld">抽奖指令</label><input id="f_gacha_cmd" placeholder="夏日抽奖"></div>
   <div><label class="fld">每日次数</label><input id="f_gacha_limit" type="number" value="5"></div>
  </div>
  <div class="row"><div style="flex:1"><label class="fld">抽奖价格（如：贝壳 10）</label><input id="f_gacha_cost" placeholder="贝壳 10"></div></div>
  <div id="event_gacha_pool"></div>
  <button class="act ghost" type="button" onclick="eventAddGacha()" style="margin-top:6px">＋ 添加奖品</button>
  <div class="sec">活动副本</div>
  <div id="event_dungeons"></div>
  <button class="act ghost" type="button" onclick="eventAddDungeon()" style="margin-top:6px">＋ 添加副本</button>
  <div class="sec">世界 Boss</div>
  <div class="chk"><input id="f_boss_enabled" type="checkbox"><label for="f_boss_enabled">启用世界 Boss</label></div>
  <div class="row">
   <div style="flex:1"><label class="fld">挑战指令</label><input id="f_boss_cmd" placeholder="夏日Boss"></div>
   <div style="flex:1"><label class="fld">Boss名称</label><input id="f_boss_name" placeholder="深海巨鲸"></div>
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
  <div class="sec" style="margin-top:10px">击杀奖励（每条奖励都会发放，可设置随机数量）</div>
  <div id="event_boss_rewards"></div>
  <button class="act ghost" type="button" onclick="eventAddBossReward()" style="margin-top:6px">＋ 添加击杀奖励</button>
  <div class="muted" style="margin-top:10px">高级用户仍可在下方「高级编辑」中直接修改 JSON。表单保存时会覆盖表单内容到 JSON。</div>`;
 return `
  <div class="muted">套餐面额（空或 0 表示不含该项，可任意组合）；或填「授权天数」改为群授权卡。</div>
  <div class="row"><div><label class="fld">金币</label><input id="f_r_coin" type="number"></div>
  <div><label class="fld">积分</label><input id="f_r_jifen" type="number"></div>
  <div><label class="fld">钻石</label><input id="f_r_diamond" type="number"></div>
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
  g('petbox').innerHTML=buildPetForm(v.pet);
  g('bagbox').innerHTML=buildBag(v.bag);
 }
 else if(cur==='groups'){g('f_enabled').checked=!!v.enabled;g('f_cross').checked=!!v.cross;}
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
  eventRenderBossRewards(bs.kill_rewards||[]);
 }
 else{const r=cardRewards(v);g('f_r_coin').value=r['金币']||'';g('f_r_jifen').value=r['积分']||'';g('f_r_diamond').value=r['钻石']||'';g('f_authdays').value=v.auth_days||'';g('f_used').checked=!!v.used;}
}
function applyFields(v){
 if(cur==='players'){
  v.coin=+g('f_coin').value||0;v.jifen=+g('f_jifen').value||0;v.diamond=+g('f_diamond').value||0;
  v.stats=v.stats||{};v.stats.battle_win=+g('f_st_win').value||0;v.stats.explore=+g('f_st_exp').value||0;
  if(g('f_haspet')&&g('f_haspet').checked){
   const pet=(v.pet&&typeof v.pet==='object')?v.pet:{};
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
   v.pet=pet;
  }else{v.pet=null;}
  const bag={};document.querySelectorAll('#bagbox .bagrow').forEach(r=>{const n=r.querySelector('.bagname').value.trim();const c=+r.querySelector('.bagcnt').value||0;if(n&&c>0)bag[n]=c;});
  v.bag=bag;
 }
 else if(cur==='groups'){v.enabled=g('f_enabled').checked;v.cross=g('f_cross').checked;}
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
   pool:eventCollectGacha()
  };
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
   kill_rewards:eventCollectBossRewards()
  };
 }
 else{const ad=+g('f_authdays').value||0;if(ad>0){v.auth_days=ad;delete v.rewards;delete v.currency;delete v.amount;}else{const r={};const c=+g('f_r_coin').value||0,j=+g('f_r_jifen').value||0,d=+g('f_r_diamond').value||0;if(c>0)r['金币']=c;if(j>0)r['积分']=j;if(d>0)r['钻石']=d;v.rewards=r;delete v.currency;delete v.amount;delete v.auth_days;}v.used=g('f_used').checked;}
 return v;
}
function g(id){return document.getElementById(id);}
function editRow(k){openModal(k,JSON.parse(JSON.stringify(cache[k]||{})));}
function addRow(){openModal('',{});}
function keyLabel(){return cur==='players'?'玩家键（群号\\x1fQQ号）':cur==='groups'?'群号':cur==='events'?'活动ID':'卡密码';}
function openModal(k,v){
 editKey=k;
 g('mtitle').textContent=k?'编辑记录':'新增记录';
 g('mfields').innerHTML=(k?'':`<label class="fld">${keyLabel()}</label><input id="newkey" style="width:100%">`)+fieldHtml();
 g('msub').textContent=k?k:'';
 if(cur==='events' && !k){
  const now=Math.floor(Date.now()/1000);
  v={
   id:'',
   name:'清凉一夏',
   enabled:true,
   start_at:now,
   end_at:now+30*86400,
   token:'贝壳',
   theme:'summer',
   menu_cmd:'夏日活动',
   dungeon_list_cmd:'活动副本',
   dungeon_enter_cmd:'进入活动副本',
   actions:{
    '赶海':{energy:10,cooldown:600,daily_limit:5,rewards:{贝壳:{min:3,max:8,chance:1}},msg:'🌊 你在礁石边翻到 {贝壳} 个贝壳！'},
    '冲浪':{energy:15,cooldown:900,daily_limit:3,rewards:{贝壳:{min:5,max:12,chance:1},经验:{min:50,max:120,chance:0.3}},msg:'🏄 冲浪收获 {贝壳} 个贝壳！'}
   },
   event_items:{
    '夏日冰饮':{category:'药品',usable:true,desc:'清凉解暑，恢复 200 点精力并回满心情。',effect:{heal_energy:200,mood:5}},
    '游泳圈':{category:'装饰',usable:false,desc:'夏日活动限定装饰道具，可佩戴在宠物身上（收藏用）。',effect:{}},
    '遮阳帽':{category:'道具',usable:true,desc:'戴上后永久增加 20 点攻击。',effect:{add_atk:20}}
   },
   shop:{
    '夏日冰饮':{cost:{贝壳:20},stock:{per_player:5},reward:{item:'夏日冰饮',count:1},desc:'恢复 200 精力并回满心情'},
    '遮阳帽':{cost:{贝壳:80},stock:{per_player:1},reward:{effect:{add_atk:20}},desc:'永久攻击 +20'}
   },
   gacha:{enabled:true,cmd:'夏日抽奖',cost:{贝壳:10},daily_limit:5,pool:[
    {weight:50,reward:{贝壳:5},msg:'安慰奖'},
    {weight:30,reward:{item:'夏日冰饮',count:1}},
    {weight:15,reward:{金币:500}},
    {weight:4,reward:{effect:{add_hp_max:50}}},
    {weight:1,reward:{item:'史诗卡',count:1},msg:'🎉 大奖！'}
   ]},
   dungeons:{
    '珊瑚洞穴':{monster:'巨蟹守卫',level_req:10,energy:15,cooldown:600,power:1500,exp:200,jifen:100,token_reward:10,reward:{item:'夏日冰饮',count:1}},
    '沉船海湾':{monster:'幽灵船长',level_req:30,energy:25,cooldown:900,power:5000,exp:500,jifen:300,token_reward:25,reward:{item:'史诗卡',count:1}}
   },
   boss:{enabled:true,cmd:'夏日Boss',name:'深海巨鲸',hp:100000,level_req:20,energy:30,cooldown:1800,daily_limit:3,damage_factor:0.1,token_per_hit:20,respawn_seconds:3600,boss_damage:200,kill_rewards:[
    {weight:50,reward:{贝壳:100,贝壳_max:200},msg:'海量贝壳'},
    {weight:30,reward:{item:'夏日冰饮',count:1,count_max:3}},
    {weight:15,reward:{effect:{add_atk:50}}},
    {weight:4,reward:{金币:1000,金币_max:5000}},
    {weight:1,reward:{item:'混沌卡',count:1},msg:'🎉 混沌品质卡！'}
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
 const r=await api('/api/upsert',{table:cur,key:key,value:v});
 if(!r.ok){alert(r.msg||'保存失败');return;}
 closeModal();load();
}
function closeModal(){g('modal').style.display='none';editKey=null;}
async function delRow(k){if(!confirm('确认删除 '+k+' ?'))return;await api('/api/delete',{table:cur,key:k});load();}
async function genCards(){
 const authdays=+g('amt_authdays').value||0;
 let payload;
 if(authdays>0){
  payload={auth_days:authdays,count:+g('cnt').value,prefix:g('pre').value};
 }else{
  const rewards={};const c=+g('amt_coin').value||0,j=+g('amt_jifen').value||0,d=+g('amt_diamond').value||0;
  if(c>0)rewards['金币']=c;if(j>0)rewards['积分']=j;if(d>0)rewards['钻石']=d;
  if(!Object.keys(rewards).length){alert('请填写金币/积分/钻石面额，或填写授权天数生成群授权卡');return;}
  payload={rewards:rewards,count:+g('cnt').value,prefix:g('pre').value};
 }
 const r=await api('/api/cards/generate',payload);
 if(!r.ok){alert(r.msg||'生成失败');return;}
 g('genout').innerHTML='✅ 已生成 '+r.codes.length+' 张：<br>'+r.codes.map(esc).join('<br>');
 load();
}
function exportUnused(){
 const lines=[];for(const k of Object.keys(cache)){const v=cache[k];if(v.used)continue;let pkg;if(+(v.auth_days||0)>0){pkg='群授权'+v.auth_days+'天';}else{const r=cardRewards(v);pkg=['金币','积分','钻石'].filter(c=>r[c]).map(c=>c+'+'+r[c]).join('/');}lines.push(`${k}\\t${pkg}`);}
 if(!lines.length){alert('没有未使用的卡密');return;}
 const blob=new Blob([lines.join('\\n')],{type:'text/plain'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='unused_cards.txt';a.click();
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
  const m=part.trim().match(/^(.+?)\\s+(\\d+)$/);
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
  detail=`<div class="row"><div style="flex:2"><label>货币</label><select class="ev-r-cur">${META.currencies.map(o=>`<option ${o===k?'selected':''}>${o}</option>`).join('')}</select></div><div style="flex:1"><label>最小值</label><input class="ev-r-curv" type="number" value="${v}"></div><div style="flex:1"><label>最大值</label><input class="ev-r-curv-max" type="number" value="${vmax!==undefined?vmax:''}" placeholder="固定"></div></div>`;
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
 else if(type==='currency') detail=`<div class="row"><div style="flex:2"><label>货币</label><select class="ev-r-cur">${(META.currencies||['金币','积分','钻石']).map(o=>`<option>${o}</option>`).join('')}</select></div><div style="flex:1"><label>最小值</label><input class="ev-r-curv" type="number" value="0"></div><div style="flex:1"><label>最大值</label><input class="ev-r-curv-max" type="number" value="" placeholder="固定"></div></div>`;
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
 return `<div class="event-card" style="border:1px solid #334155;padding:10px;margin:8px 0;border-radius:8px">
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
 div.innerHTML=`<div class="reward-row row" style="align-items:flex-end;margin:6px 0;border:1px dashed #334155;padding:8px;border-radius:6px">
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
  div.innerHTML=`<div class="reward-row row" style="align-items:flex-end;margin:6px 0;border:1px dashed #334155;padding:8px;border-radius:6px">
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
 return `<div class="event-card" style="border:1px solid #334155;padding:10px;margin:8px 0;border-radius:8px">
  <div class="row">
   <div style="flex:2"><label>商品名</label><input class="ev-s-name" value="${escA(name)}"></div>
   <div style="flex:2"><label>价格（如：贝壳 20 / 金币 100）</label><input class="ev-s-cost" value="${escA(eventCostToString(it.cost||{}))}"></div>
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
 return `<div class="event-card" style="border:1px solid #334155;padding:10px;margin:8px 0;border-radius:8px">
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
 return `<div class="event-card" style="border:1px solid #334155;padding:10px;margin:8px 0;border-radius:8px">
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
   <div style="flex:1"><label>经验</label><input class="ev-d-exp" type="number" value="${conf.exp!==undefined?conf.exp:0}"></div>
   <div style="flex:1"><label>积分</label><input class="ev-d-jifen" type="number" value="${conf.jifen!==undefined?conf.jifen:0}"></div>
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
 return `<div class="event-card" style="border:1px solid #334155;padding:10px;margin:8px 0;border-radius:8px">
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
 return `<div class="event-card" style="border:1px solid #334155;padding:10px;margin:8px 0;border-radius:8px">
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

(async()=>{await loadMeta();await load();})();

</script></body></html>"""
